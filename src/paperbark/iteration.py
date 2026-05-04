"""Per-iteration log processing: raw text → JSON summary.

Reads raw log lines, parses any embedded JSON record, and emits the
summary shape that :func:`paperbark.aggregate.merge_iteration` consumes.
Optionally writes a flat per-line CSV alongside the summary for
ad-hoc spreadsheet inspection.

Output shape (also written to disk as JSON)::

    {
        "meta": {"source", "total_lines", "parsed", "failed_to_parse", "generated_at"},
        "level_counts":     {minute: {level: count, ...}},
        "component_counts": {minute: {component: count, ...}},
        "event_counts":     {minute: [{"event": "comp: msg", "count": N}, ...]},
        "warn_error_counts": {"comp: msg": N, ...},
    }
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_LOCAL_TZ = ZoneInfo("Australia/Melbourne")
DEFAULT_TIMESTAMP_KEYS = ("time", "timestamp", "@timestamp", "ts", "created_at")
DEFAULT_LEVEL_KEYS = ("level",)
DEFAULT_MESSAGE_KEYS = ("msg", "message")
DEFAULT_COMPONENT_KEYS = ("component",)
_FLAT_COLUMNS = ("timestamp", "level", "component", "message", "extras")
_UNKNOWN = "unknown"

# Field-name → default key tuple. ``format_keys`` arguments override these
# entries individually; unmentioned fields keep their defaults so a partial
# override (just a custom timestamp key, say) doesn't lose level/message
# detection.
_DEFAULT_FORMAT_KEYS: dict[str, tuple[str, ...]] = {
    "timestamp": DEFAULT_TIMESTAMP_KEYS,
    "level": DEFAULT_LEVEL_KEYS,
    "message": DEFAULT_MESSAGE_KEYS,
    "component": DEFAULT_COMPONENT_KEYS,
}
FORMAT_KEY_FIELDS: tuple[str, ...] = tuple(_DEFAULT_FORMAT_KEYS.keys())


def _resolved_format_keys(
    overrides: dict[str, tuple[str, ...]] | None,
) -> dict[str, tuple[str, ...]]:
    """Merge caller overrides with built-in defaults.

    Returning a fresh dict each call keeps the module-level defaults
    immutable; the loader rejects unknown override keys upstream so a
    typo can't silently disable detection.
    """
    resolved = dict(_DEFAULT_FORMAT_KEYS)
    if overrides:
        resolved.update(overrides)
    return resolved


def summarise_lines(
    lines: Iterable[str],
    *,
    source: str = "",
    flat_rows: list[dict[str, str]] | None = None,
    format_keys: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Summarise an iterable of raw log ``lines``.

    When ``flat_rows`` is supplied, one row is appended per parsed
    record (lines that fail to parse are skipped for the flat output).
    The summary dict has the canonical shape consumed by
    :func:`paperbark.aggregate.merge_iteration`.

    ``format_keys`` overrides the per-field JSON key tuples this function
    consults when extracting timestamp / level / message / component from
    a parsed record. Unspecified fields keep their defaults; the loader
    is responsible for rejecting unknown field names so a typo can't
    silently disable a field.
    """
    keys = _resolved_format_keys(format_keys)
    timestamp_keys = keys["timestamp"]
    level_keys = keys["level"]
    message_keys = keys["message"]
    component_keys = keys["component"]
    core_fields = frozenset({*timestamp_keys, *level_keys, *message_keys, *component_keys})

    level_counts: dict[str, Counter[str]] = defaultdict(Counter)
    component_counts: dict[str, Counter[str]] = defaultdict(Counter)
    event_counts: dict[str, Counter[str]] = defaultdict(Counter)
    warn_error_counts: Counter[str] = Counter()
    total = 0
    parsed = 0
    errors = 0
    for line in lines:
        total += 1
        record = _try_parse_json_record(line)
        if record is None:
            errors += 1
            continue
        parsed += 1
        minute = _minute_key(record, timestamp_keys)
        level = str(_first_string(record, level_keys) or _UNKNOWN).lower()
        component = str(_first_string(record, component_keys) or _UNKNOWN)
        raw_message = _first_string(record, message_keys) or "<no message>"
        message = _strip_component_prefix(raw_message, component)
        event = f"{component}: {message}"

        level_counts[minute][level] += 1
        component_counts[minute][component] += 1
        event_counts[minute][event] += 1
        if level in ("warn", "error"):
            warn_error_counts[event] += 1

        if flat_rows is not None:
            extras = {k: v for k, v in record.items() if k not in core_fields}
            flat_rows.append(
                {
                    "timestamp": _full_timestamp(record, timestamp_keys),
                    "level": level,
                    "component": component,
                    "message": message,
                    "extras": json.dumps(extras, separators=(",", ":")) if extras else "",
                }
            )

    return {
        "meta": {
            "source": source,
            "total_lines": total,
            "parsed": parsed,
            "failed_to_parse": errors,
            "generated_at": datetime.now(_LOCAL_TZ).isoformat(),
        },
        "level_counts": {m: dict(c) for m, c in level_counts.items()},
        "component_counts": {m: dict(c) for m, c in component_counts.items()},
        "event_counts": {
            m: [
                {"event": event, "count": count}
                for event, count in sorted(c.items(), key=lambda x: -x[1])
            ]
            for m, c in event_counts.items()
        },
        "warn_error_counts": dict(warn_error_counts),
    }


def summarise_log_file(
    raw_path: Path,
    *,
    flat_csv_path: Path | None = None,
    format_keys: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """Summarise the raw log file at ``raw_path``.

    Always returns the summary dict. When ``flat_csv_path`` is supplied
    a flat per-line CSV is written there as a side effect. ``format_keys``
    is forwarded to :func:`summarise_lines` for per-source key overrides.
    """
    flat_rows: list[dict[str, str]] | None = [] if flat_csv_path else None
    with raw_path.open("r", encoding="utf-8", errors="ignore") as handle:
        summary = summarise_lines(
            handle,
            source=str(raw_path),
            flat_rows=flat_rows,
            format_keys=format_keys,
        )
    if flat_csv_path is not None and flat_rows is not None:
        write_flat_csv(flat_csv_path, flat_rows)
    return summary


def write_flat_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    """Write a flat per-line CSV with a fixed column set."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_FLAT_COLUMNS))
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def cli(argv: list[str] | None = None) -> int:
    """Stand-alone CLI matching ``reference/process_logs.py``."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2:
        sys.stderr.write("usage: python -m paperbark.iteration <raw_log_file> <output_json>\n")
        return 1
    raw_path = Path(args[0])
    output_path = Path(args[1])
    if not raw_path.exists():
        sys.stderr.write(f"error: raw log file not found: {raw_path}\n")
        return 1
    flat_csv_path = output_path.with_suffix(".csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = summarise_log_file(raw_path, flat_csv_path=flat_csv_path)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    meta = summary["meta"]
    sys.stdout.write(
        f"processed {meta['parsed']}/{meta['total_lines']} lines from "
        f"{raw_path.name}; summary written to {output_path.name}\n"
    )
    return 0


# --- Internals --------------------------------------------------------------


def _try_parse_json_record(line: str) -> dict[str, Any] | None:
    idx = line.find("{")
    if idx == -1:
        return None
    try:
        record = json.loads(line[idx:])
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _first_string(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = record.get(key)
        if value:
            return str(value)
    return ""


def _minute_key(record: dict[str, Any], timestamp_keys: tuple[str, ...]) -> str:
    raw = _first_string(record, timestamp_keys)
    if not raw:
        return _UNKNOWN
    cleaned = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return raw[:16] if len(raw) >= 16 else raw
    return parsed.strftime("%Y-%m-%dT%H:%M")


def _full_timestamp(record: dict[str, Any], timestamp_keys: tuple[str, ...]) -> str:
    raw = _first_string(record, timestamp_keys)
    if not raw:
        return ""
    cleaned = raw.replace("Z", "+00:00")
    try:
        # Preserve the source offset — two instants with different offsets would
        # otherwise collapse to identical CSV rows after UTC conversion.
        return datetime.fromisoformat(cleaned).isoformat(timespec="seconds")
    except ValueError:
        return raw[:19] if len(raw) >= 19 else raw


def _strip_component_prefix(message: str, component: str) -> str:
    if not component or component == _UNKNOWN:
        return message
    prefix = f"[{component}]"
    return message[len(prefix) :].lstrip() if message.startswith(prefix) else message


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(cli())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
