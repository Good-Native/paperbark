"""Aggregate per-iteration log summaries into time-series outputs.

Consumes the per-iteration JSON files produced by the (forthcoming)
``paperbark.iteration`` module and emits four artefacts in the run
directory:

- ``time_series.csv``: minute | debug | info | warn | error
- ``events_per_minute.csv``: minute | top-50 ``component: message`` columns
- ``components_per_minute.csv``: minute | every component column
- ``summary.md``: human-readable rollup

Incremental mode (the default) fingerprints each input file by
``mtime_ns + size`` and skips ones whose fingerprint matches the last
run. Two caveats carried over from the reference port:

1. A file rewritten in place with the same mtime and size has the same
   fingerprint, so its new content is silently skipped.
2. A file whose fingerprint *did* change is reprocessed, but its prior
   contribution is not subtracted from the running totals — when this
   port detects a previously-seen filename with a new fingerprint it
   discards the cached aggregate and rebuilds from scratch.

Pure append-only workloads (new files added, existing files never
rewritten) are always handled correctly by incremental mode.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DATA_FILE_NAME = ".aggregate_data.json"
LEVELS = ("debug", "info", "warn", "error")
TOP_EVENTS_DEFAULT = 50
SUMMARY_RECENT_MINUTES = 30
SUMMARY_TOP_EVENTS = 30
SUMMARY_TOP_WARN_ERROR = 20
SUMMARY_EVENT_TRIM = 80
_LOCAL_TZ = ZoneInfo("Australia/Melbourne")


@dataclass(slots=True)
class MinuteBucket:
    """One minute's worth of aggregated counts."""

    samples: int = 0
    level_counts: dict[str, int] = field(default_factory=dict)
    event_counts: dict[str, int] = field(default_factory=dict)
    component_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class AggregateState:
    """Lossless aggregate state for one run."""

    by_minute: dict[str, MinuteBucket] = field(default_factory=dict)
    total_lines: int = 0
    failed_to_parse: int = 0
    warn_error_counts: dict[str, int] = field(default_factory=dict)
    processed_files: dict[str, str] = field(default_factory=dict)


def merge_iteration(state: AggregateState, payload: Mapping[str, Any]) -> None:
    """Merge one iteration JSON ``payload`` into ``state`` in place.

    Staged locally first so a malformed sub-section does not leave the
    state half-updated. Raises ``ValueError`` if the payload shape is
    unparseable; callers decide whether that aborts or just skips.
    """
    meta = payload.get("meta") or {}
    file_total_lines = int(meta.get("total_lines", 0))
    file_failed = int(meta.get("failed_to_parse", 0))

    staged_minutes: dict[str, MinuteBucket] = defaultdict(MinuteBucket)
    staged_warn_error: dict[str, int] = defaultdict(int)

    level_counts_in = payload.get("level_counts") or {}
    if not isinstance(level_counts_in, Mapping):
        raise ValueError("level_counts must be a mapping")
    for ts, levels in level_counts_in.items():
        minute = _minute_key(ts)
        bucket = staged_minutes[minute]
        bucket.samples += 1
        for level, count in (levels or {}).items():
            bucket.level_counts[level] = bucket.level_counts.get(level, 0) + int(count)

    event_counts_in = payload.get("event_counts") or {}
    if not isinstance(event_counts_in, Mapping):
        raise ValueError("event_counts must be a mapping")
    for ts, events in event_counts_in.items():
        minute = _minute_key(ts)
        bucket = staged_minutes[minute]
        for item in events or []:
            event = item.get("event", "unknown")
            count = int(item.get("count", 0))
            bucket.event_counts[event] = bucket.event_counts.get(event, 0) + count

    component_counts_in = payload.get("component_counts") or {}
    if not isinstance(component_counts_in, Mapping):
        raise ValueError("component_counts must be a mapping")
    for ts, components in component_counts_in.items():
        minute = _minute_key(ts)
        bucket = staged_minutes[minute]
        for component, count in (components or {}).items():
            bucket.component_counts[component] = bucket.component_counts.get(component, 0) + int(
                count
            )

    warn_error_in = payload.get("warn_error_counts") or {}
    if not isinstance(warn_error_in, Mapping):
        raise ValueError("warn_error_counts must be a mapping")
    for event, count in warn_error_in.items():
        staged_warn_error[event] += int(count)

    # All staging succeeded — commit atomically.
    state.total_lines += file_total_lines
    state.failed_to_parse += file_failed
    for event, count in staged_warn_error.items():
        state.warn_error_counts[event] = state.warn_error_counts.get(event, 0) + count
    for minute, staged_bucket in staged_minutes.items():
        target = state.by_minute.setdefault(minute, MinuteBucket())
        target.samples += staged_bucket.samples
        for level, count in staged_bucket.level_counts.items():
            target.level_counts[level] = target.level_counts.get(level, 0) + count
        for event, count in staged_bucket.event_counts.items():
            target.event_counts[event] = target.event_counts.get(event, 0) + count
        for component, count in staged_bucket.component_counts.items():
            target.component_counts[component] = target.component_counts.get(component, 0) + count


def load_state(run_dir: Path) -> AggregateState:
    """Read the persisted aggregate state for ``run_dir``, or a fresh state."""
    data_file = run_dir / DATA_FILE_NAME
    if not data_file.exists():
        return AggregateState()
    try:
        raw = json.loads(data_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"warn: could not load {data_file}: {exc}\n")
        return AggregateState()
    return _state_from_raw(raw)


def save_state(run_dir: Path, state: AggregateState) -> None:
    """Persist ``state`` to ``run_dir`` atomically (tmp file + rename)."""
    payload = {
        "last_update": datetime.now(_LOCAL_TZ).isoformat(),
        "processed_files": dict(sorted(state.processed_files.items())),
        "total_lines": state.total_lines,
        "failed_to_parse": state.failed_to_parse,
        "warn_error_counts": dict(state.warn_error_counts),
        "by_minute": {
            minute: {
                "samples": bucket.samples,
                "level_counts": dict(bucket.level_counts),
                "event_counts": dict(bucket.event_counts),
                "component_counts": dict(bucket.component_counts),
            }
            for minute, bucket in state.by_minute.items()
        },
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    data_file = run_dir / DATA_FILE_NAME
    tmp_file = run_dir / f"{DATA_FILE_NAME}.tmp"
    tmp_file.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp_file.replace(data_file)


def write_time_series(csv_path: Path, state: AggregateState) -> None:
    """Write minute | debug | info | warn | error to ``csv_path``."""
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", *LEVELS])
        for minute in sorted(state.by_minute):
            levels = state.by_minute[minute].level_counts
            writer.writerow([minute, *(levels.get(level, 0) for level in LEVELS)])


def write_events_csv(
    csv_path: Path, state: AggregateState, top_n: int = TOP_EVENTS_DEFAULT
) -> None:
    """Write minute | top-N event columns to ``csv_path``."""
    totals: dict[str, int] = defaultdict(int)
    for bucket in state.by_minute.values():
        for event, count in bucket.event_counts.items():
            totals[event] += count
    top_events = [e for e, _ in sorted(totals.items(), key=lambda x: -x[1])[:top_n]]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", *top_events])
        for minute in sorted(state.by_minute):
            counts = [state.by_minute[minute].event_counts.get(e, 0) for e in top_events]
            writer.writerow([minute, *counts])


def write_components_csv(csv_path: Path, state: AggregateState) -> None:
    """Write minute | component columns to ``csv_path``."""
    components = sorted({c for bucket in state.by_minute.values() for c in bucket.component_counts})
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", *components])
        for minute in sorted(state.by_minute):
            counts = [state.by_minute[minute].component_counts.get(c, 0) for c in components]
            writer.writerow([minute, *counts])


def write_summary(summary_path: Path, state: AggregateState, new_files_count: int) -> None:
    """Write a human-readable markdown summary to ``summary_path``."""
    now = datetime.now(_LOCAL_TZ)
    event_totals: dict[str, int] = defaultdict(int)
    for bucket in state.by_minute.values():
        for event, count in bucket.event_counts.items():
            event_totals[event] += count
    minute_keys = sorted(state.by_minute)
    parse_rate = 100 * (1 - state.failed_to_parse / max(state.total_lines, 1))
    lines: list[str] = [
        "# Log aggregation summary",
        "",
        f"**Generated:** {now.isoformat()}",
        "",
        f"**New files processed:** {new_files_count}",
        "",
    ]
    if minute_keys:
        lines += [f"**Time range:** {minute_keys[0]} to {minute_keys[-1]}", ""]
    lines += [
        f"- Total log lines: **{state.total_lines:,}**",
        f"- Parse success rate: **{parse_rate:.1f}%**",
        "",
        "## Log levels by minute",
        "",
        "| Timestamp | Debug | Info | Warn | Error |",
        "|-----------|-------|------|------|-------|",
    ]
    for minute in minute_keys[-SUMMARY_RECENT_MINUTES:]:
        levels = state.by_minute[minute].level_counts
        lines.append(
            f"| {minute} | {levels.get('debug', 0)} | {levels.get('info', 0)} |"
            f" {levels.get('warn', 0)} | {levels.get('error', 0)} |"
        )
    lines += ["", "## Top events", "", "| Count | Event |", "|-------|-------|"]
    for event, count in sorted(event_totals.items(), key=lambda x: -x[1])[:SUMMARY_TOP_EVENTS]:
        lines.append(f"| {count:,} | {_md_escape(event)} |")
    warn_error_sorted = sorted(state.warn_error_counts.items(), key=lambda x: -x[1])[
        :SUMMARY_TOP_WARN_ERROR
    ]
    if warn_error_sorted:
        lines += [
            "",
            "## Errors and warnings",
            "",
            "| Count | Event |",
            "|-------|-------|",
        ]
        for event, count in warn_error_sorted:
            lines.append(f"| {count:,} | {_md_escape(event)} |")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def aggregate(run_dir: Path, *, full: bool = False) -> bool:
    """Aggregate every iteration JSON in ``run_dir`` and write outputs.

    Returns ``True`` if any output was produced. ``full=True`` ignores
    cached state and rebuilds from scratch.
    """
    if not run_dir.exists():
        sys.stderr.write(f"error: directory {run_dir} does not exist\n")
        return False
    state = load_state(run_dir) if not full else AggregateState()
    json_files = sorted(p for p in run_dir.glob("*.json") if not p.name.startswith("."))
    new_files, fingerprints, rewritten = _select_new_files(state, json_files)
    if rewritten and not full:
        sys.stderr.write("info: rewritten file(s) detected; rebuilding aggregate from scratch\n")
        state = AggregateState()
        new_files = list(json_files)
        fingerprints = {p.name: _file_fingerprint(p) for p in json_files}
    if not new_files and full:
        sys.stderr.write(f"warn: no JSON files found in {run_dir}\n")
        return False
    success = 0
    for path in new_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"warn: could not read {path}: {exc}\n")
            continue
        try:
            merge_iteration(state, payload)
        except ValueError as exc:
            sys.stderr.write(f"warn: bad shape in {path}: {exc}\n")
            continue
        state.processed_files[path.name] = fingerprints[path.name]
        success += 1
    write_time_series(run_dir / "time_series.csv", state)
    write_events_csv(run_dir / "events_per_minute.csv", state)
    write_components_csv(run_dir / "components_per_minute.csv", state)
    write_summary(run_dir / "summary.md", state, success)
    save_state(run_dir, state)
    return True


def cli(argv: list[str] | None = None) -> int:
    """Stand-alone CLI matching ``reference/aggregate_logs.py``."""
    import argparse

    parser = argparse.ArgumentParser(description="Aggregate per-iteration log summaries.")
    parser.add_argument("run_dir", help="Run directory containing iteration *.json files")
    parser.add_argument(
        "--full", action="store_true", help="Force a full rebuild, ignoring cached state."
    )
    args = parser.parse_args(argv)
    ok = aggregate(Path(args.run_dir), full=args.full)
    return 0 if ok else 1


# --- Internals --------------------------------------------------------------


def _minute_key(timestamp: object) -> str:
    text = str(timestamp) if timestamp is not None else ""
    return text[:16]


def _file_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _select_new_files(
    state: AggregateState, candidates: Iterable[Path]
) -> tuple[list[Path], dict[str, str], bool]:
    fingerprints: dict[str, str] = {}
    new: list[Path] = []
    rewritten = False
    for path in candidates:
        fp = _file_fingerprint(path)
        previous = state.processed_files.get(path.name)
        if previous == fp:
            continue
        if previous:
            rewritten = True
        fingerprints[path.name] = fp
        new.append(path)
    return new, fingerprints, rewritten


def _state_from_raw(raw: Mapping[str, Any]) -> AggregateState:
    state = AggregateState(
        total_lines=int(raw.get("total_lines", 0)),
        failed_to_parse=int(raw.get("failed_to_parse", 0)),
        warn_error_counts={k: int(v) for k, v in (raw.get("warn_error_counts") or {}).items()},
    )
    raw_processed = raw.get("processed_files", {})
    if isinstance(raw_processed, Mapping):
        state.processed_files = {str(k): str(v) for k, v in raw_processed.items()}
    for minute, bucket in (raw.get("by_minute") or {}).items():
        state.by_minute[minute] = MinuteBucket(
            samples=int(bucket.get("samples", 0)),
            level_counts={k: int(v) for k, v in (bucket.get("level_counts") or {}).items()},
            event_counts={k: int(v) for k, v in (bucket.get("event_counts") or {}).items()},
            component_counts={k: int(v) for k, v in (bucket.get("component_counts") or {}).items()},
        )
    return state


def _md_escape(event: str) -> str:
    return event[:SUMMARY_EVENT_TRIM].replace("|", "\\|")


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(cli())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
