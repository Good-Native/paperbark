"""Dispatcher: compose sources → cursor filter → iteration summary → aggregate.

This module wires the per-layer pieces together so ``paperbark monitor``
runs end to end. The unit of work is one *iteration*: each configured
source captures a fresh window, the cursor filter dedupes against the
previous iteration's output, the surviving lines are written to disk and
summarised, and the per-app aggregate state is refreshed.

A run-dir laid out per :data:`docs/ROADMAP.md`'s public-contract section
is created on the first call and reused for every subsequent iteration::

    logs/YYYYMMDD/HHMM_<slug>/
    ├── <app>/raw/iter_<NNN>_<HHMMSSZ>.log
    ├── <app>/.cursor
    ├── <app>/iter_<NNN>_<HHMMSSZ>.json
    ├── <app>/time_series.csv
    ├── <app>/events_per_minute.csv
    ├── <app>/components_per_minute.csv
    └── <app>/summary.md

This first cut runs **one** iteration per call; the iteration loop and
the ``rich.live`` ticker land in a follow-up PR.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from paperbark.aggregate import aggregate
from paperbark.config import Config, SourceConfig
from paperbark.cursor import filter_stream
from paperbark.iteration import summarise_log_file
from paperbark.sources import (
    CloudWatchSource,
    FileSource,
    FlyctlSource,
    KubectlSource,
    Source,
    StdinSource,
    WranglerSource,
)


class DispatcherError(ValueError):
    """Raised when configuration is internally inconsistent (unknown source
    type, missing required option, etc.). Distinct from ``ConfigError`` so
    the CLI can surface a more specific message."""


def build_source(spec: SourceConfig) -> Source:
    """Build a :class:`Source` from a parsed :class:`SourceConfig`.

    Raises :class:`DispatcherError` for unknown types or missing required
    options. The flyctl source is the only one currently usable; the
    stubs return Protocol-conformant instances so ``paperbark init`` /
    config validation can still resolve them.
    """
    if spec.type == "flyctl":
        app = spec.options.get("app")
        if not isinstance(app, str) or not app:
            raise DispatcherError(f"source {spec.name!r}: 'app' is required for flyctl")
        no_tail = bool(spec.options.get("no_tail", True))
        return FlyctlSource(app=app, no_tail=no_tail)
    if spec.type == "wrangler":
        return WranglerSource()
    if spec.type == "kubectl":
        return KubectlSource()
    if spec.type == "cloudwatch":
        return CloudWatchSource()
    if spec.type == "file":
        return FileSource()
    if spec.type == "stdin":
        return StdinSource()
    raise DispatcherError(f"source {spec.name!r}: unknown type {spec.type!r}")


def build_sources(config: Config) -> list[tuple[str, Source]]:
    """Build all sources declared in ``config`` as ``(name, source)`` pairs."""
    return [(spec.name, build_source(spec)) for spec in config.sources]


def new_run_dir(root: Path, *, now: datetime | None = None) -> Path:
    """Create and return a fresh run directory under ``root``.

    Layout: ``<root>/<YYYYMMDD>/<HHMM>``. ``now`` is injectable for tests
    so the path is deterministic.
    """
    moment = now if now is not None else datetime.now()
    date_part = moment.strftime("%Y%m%d")
    time_part = moment.strftime("%H%M")
    run_dir = root / date_part / time_part
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def capture_iteration(
    source: Source,
    app_dir: Path,
    iteration: int,
    *,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Capture one iteration for a single source.

    Lines from ``source.capture()`` are passed through the cursor filter
    against ``app_dir/.cursor`` and written to a fresh
    ``app_dir/raw/iter_<NNN>_<HHMMSSZ>.log``. The raw log is then
    summarised into ``app_dir/iter_<NNN>_<HHMMSSZ>.json`` (the same
    shape :func:`paperbark.aggregate.merge_iteration` consumes).

    Returns ``(raw_log_path, summary_json_path)``.
    """
    moment = now if now is not None else datetime.now(tz=UTC)
    timestamp = moment.strftime("%Y%m%dT%H%M%SZ")
    iteration_label = f"iter_{iteration:04d}_{timestamp}"

    raw_dir = app_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_log = raw_dir / f"{iteration_label}.log"
    summary_json = app_dir / f"{iteration_label}.json"
    cursor_path = app_dir / ".cursor"

    cursor = ""
    if cursor_path.exists():
        cursor = cursor_path.read_text(encoding="utf-8").strip()

    with raw_log.open("w", encoding="utf-8") as f:
        new_cursor = filter_stream(source.capture(), cursor, write=f.write)

    if new_cursor and new_cursor != cursor:
        cursor_path.write_text(new_cursor, encoding="utf-8")

    summary = summarise_log_file(raw_log)
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return raw_log, summary_json


def run_iteration(
    built_sources: Sequence[tuple[str, Source]],
    run_dir: Path,
    iteration: int,
    *,
    now: datetime | None = None,
) -> None:
    """Run one iteration across every built source.

    Captures + summarises per source, then refreshes the per-app
    aggregate (time-series CSVs and ``summary.md``).
    """
    for name, source in built_sources:
        app_dir = run_dir / name
        capture_iteration(source, app_dir, iteration, now=now)
        aggregate(app_dir)


def run_monitor(
    config: Config,
    *,
    built_sources: Sequence[tuple[str, Source]] | None = None,
    now: datetime | None = None,
) -> Path:
    """Top-level entry: create a run dir, build sources, run one iteration.

    ``built_sources`` lets tests bypass :func:`build_sources` and inject
    pre-built fakes. Returns the run-dir path so callers can print it.
    """
    if not config.sources and built_sources is None:
        raise DispatcherError(
            "no sources configured; add at least one [[sources]] entry to paperbark.toml"
        )
    sources = built_sources if built_sources is not None else build_sources(config)
    run_dir = new_run_dir(config.root, now=now)
    run_iteration(sources, run_dir, iteration=1, now=now)
    return run_dir
