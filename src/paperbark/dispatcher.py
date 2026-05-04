"""Dispatcher: compose sources → cursor filter → iteration summary → aggregate.

This module wires the per-layer pieces together so ``paperbark monitor``
runs end to end. The unit of work is one *iteration*: each configured
source captures a fresh window, the cursor filter dedupes against the
previous iteration's output, the surviving lines are written to disk and
summarised, and the per-app aggregate state is refreshed.

A run-dir laid out per :data:`docs/ROADMAP.md`'s public-contract section
is created on the first call and reused for every subsequent iteration::

    logs/YYYYMMDD/HHMM_<slug>_<settings>/
    ├── <app>/raw/iter_<NNN>_<HHMMSSZ>.log
    ├── <app>/.cursor
    ├── <app>/iter_<NNN>_<HHMMSSZ>.json
    ├── <app>/time_series.csv
    ├── <app>/events_per_minute.csv
    ├── <app>/components_per_minute.csv
    ├── <app>/summary.md
    ├── snapshots/analysis_<HHMMSSZ>.{json,md}   # written every analyse_every
    ├── analysis.{json,md}                       # final probe report
    └── monitor.log                              # per-iteration ticker log

The single-iteration helpers :func:`capture_iteration` / :func:`run_iteration`
remain available for tests and ad-hoc tooling; :func:`run_monitor_loop`
wraps them in the cadence-driven loop the CLI uses.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from paperbark.aggregate import aggregate
from paperbark.config import Config, MonitorConfig, SourceConfig
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

# Type aliases for the loop's injection points. Keeping them at module scope
# avoids long inline ``Callable[...]`` annotations on every signature.
StateCallback = Callable[["MonitorState"], None]
SnapshotRunner = Callable[[Path, Path | None], None]
MonotonicClock = Callable[[], float]
WallClock = Callable[[], datetime]

# Adjective-colour slug pools mirror reference/logs.sh so concurrent runs are
# easy to distinguish at a glance. Keep these in lockstep with the bash list;
# regenerating an existing run from logs would otherwise read as a different
# slug under the Python port than under the original bash dispatcher.
_SLUG_ADJECTIVES: tuple[str, ...] = (
    "grumpy", "happy", "lazy", "quick", "brave", "silent", "loud", "sleepy",
    "hungry", "tiny", "spicy", "mellow", "plucky", "witty", "bright", "stormy",
    "frosty", "sunny", "rusty", "merry", "gentle", "clumsy", "chatty", "curious",
    "eager", "fancy", "giddy", "nimble", "proud", "sturdy",
)  # fmt: skip
_SLUG_COLOURS: tuple[str, ...] = (
    "orange", "purple", "sky", "river", "panda", "cobra", "falcon", "meadow",
    "ember", "hazel", "crimson", "teal", "indigo", "amber", "slate", "olive",
    "rose", "mint", "coral", "cobalt", "ivory", "ochre", "azure", "plum",
    "lilac", "mango", "onyx", "pearl", "sage", "saffron",
)  # fmt: skip


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


_SLUG_REPLACE = re.compile(r"[^a-zA-Z0-9_-]")
_DEFAULT_SLUG = "run"


def _safe_slug(name: str) -> str:
    """Sanitise a source name into a path-safe slug component."""
    cleaned = _SLUG_REPLACE.sub("-", name).strip("-")
    return cleaned or _DEFAULT_SLUG


def random_slug(*, rng: random.Random | None = None) -> str:
    """Return a fresh ``<adjective>-<colour>`` slug for a run.

    The ``rng`` parameter is the test seam — pass a ``random.Random(seed)`` to
    get a deterministic slug. With no argument we instantiate a non-seeded
    :class:`random.Random`, which draws from the OS entropy pool.
    """
    # The slug is only a human-readable run ID; cryptographic strength would
    # be wasted, hence the ``random`` module rather than ``secrets``.
    r = rng if rng is not None else random.Random()  # noqa: S311
    return f"{r.choice(_SLUG_ADJECTIVES)}-{r.choice(_SLUG_COLOURS)}"


def settings_suffix(interval_seconds: int, iterations: int) -> str:
    """Build the ``<settings>`` half of the run-dir name.

    Mirrors the suffix logic in ``reference/logs.sh``: ``<interval>_<duration>``,
    where ``interval`` is ``Ns`` under a minute and ``Nm`` otherwise, and
    ``duration`` is rounded to the nearest unit (minutes / hours / days). When
    ``iterations`` is 0 the suffix is ``<interval>_forever``.
    """
    if interval_seconds <= 0:
        raise ValueError(f"interval must be > 0, got {interval_seconds}")
    if iterations < 0:
        raise ValueError(f"iterations must be >= 0, got {iterations}")
    interval_str = (
        f"{interval_seconds // 60}m" if interval_seconds >= 60 else f"{interval_seconds}s"
    )
    if iterations == 0:
        return f"{interval_str}_forever"
    duration = interval_seconds * iterations
    if duration >= 86400:
        # Round-half-up per the bash implementation: `(x + half) // unit`.
        duration_str = f"{(duration + 43200) // 86400}d"
    elif duration >= 3600:
        duration_str = f"{(duration + 1800) // 3600}h"
    else:
        duration_str = f"{(duration + 30) // 60}m"
    return f"{interval_str}_{duration_str}"


def new_run_dir(
    root: Path,
    *,
    slug: str = _DEFAULT_SLUG,
    now: datetime | None = None,
) -> Path:
    """Create and return a fresh run directory under ``root``.

    Layout: ``<root>/<YYYYMMDD>/<HHMM>_<slug>`` per the public run-dir
    contract in ``CLAUDE.md`` (``HHMM_<slug>_<settings>``; v1 leaves the
    ``<settings>`` half empty). The leading ``HHMM_`` form is required —
    ``paperbark.search.resolve_runs`` filters discovery to directories
    matching exactly that shape, so a bare ``HHMM`` would be invisible.

    ``now`` is injectable for tests so the path is deterministic.
    """
    moment = now if now is not None else datetime.now()
    date_part = moment.strftime("%Y%m%d")
    time_part = moment.strftime("%H%M")
    safe = _safe_slug(slug)
    run_dir = root / date_part / f"{time_part}_{safe}"
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
    pre-built fakes. The empty-source guard runs after resolution so an
    explicitly empty injection is rejected the same way an empty config
    would be. Returns the run-dir path so callers can print it.
    """
    sources = built_sources if built_sources is not None else build_sources(config)
    if not sources:
        raise DispatcherError(
            "no sources configured; add at least one [[sources]] entry to paperbark.toml"
        )
    slug = "_".join(_safe_slug(name) for name, _ in sources)
    run_dir = new_run_dir(config.root, slug=slug, now=now)
    run_iteration(sources, run_dir, iteration=1, now=now)
    return run_dir


# --- long-running monitor --------------------------------------------------


@dataclass(frozen=True)
class MonitorState:
    """Snapshot of loop progress, published after every iteration.

    The animator (and tests) read this to keep the ticker fresh. ``finished``
    flips to ``True`` for the final state push so a TTY consumer can swap from
    "spinning" to "done" without polling.
    """

    iteration: int
    iterations_max: int
    elapsed_seconds: int
    captured_total: int
    next_snapshot_seconds: int  # -1 when snapshots disabled.
    finished: bool = False


@dataclass(frozen=True)
class MonitorResult:
    """Loop outcome surfaced to the CLI after :func:`run_monitor_loop` returns."""

    run_dir: Path
    iterations_completed: int
    captured_total: int
    stopped_early: bool


def _resolve_run_slug(monitor: MonitorConfig, rng: random.Random | None) -> str:
    """Return the user-supplied or auto-generated run slug for a monitor run."""
    if monitor.run_id:
        return monitor.run_id
    return random_slug(rng=rng)


def _count_lines(path: Path) -> int:
    """Return the line count of ``path``, tolerating missing files."""
    if not path.exists():
        return 0
    with path.open("rb") as f:
        return sum(1 for _ in f)


def _iso_log_ts(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_monitor_loop(
    config: Config,
    *,
    monitor: MonitorConfig | None = None,
    built_sources: Sequence[tuple[str, Source]] | None = None,
    stop_event: threading.Event | None = None,
    on_state: StateCallback | None = None,
    snapshot_runner: SnapshotRunner | None = None,
    rng: random.Random | None = None,
    monotonic: MonotonicClock = time.monotonic,
    clock: WallClock | None = None,
) -> MonitorResult:
    """Run ``paperbark monitor`` on a fixed cadence until stopped.

    This is the cadence-driven counterpart to :func:`run_monitor` — instead of
    capturing one iteration and returning, it loops until either ``iterations``
    iterations have completed (or forever, when ``iterations == 0``) or
    ``stop_event`` is set. The loop publishes a :class:`MonitorState` after
    every iteration via ``on_state``, runs ``snapshot_runner`` every
    ``analyse_every`` seconds, and runs the final aggregate + analyse before
    returning. Subprocess SIGINT propagates naturally; the CLI installs a
    handler that flips ``stop_event`` so the in-flight iteration finishes
    cleanly before the loop exits.

    All clock functions are injectable so tests can drive the loop forward
    without sleeping. ``monotonic`` controls elapsed/sleep budgets; ``clock``
    drives filesystem timestamps. ``built_sources`` and ``snapshot_runner``
    let tests run the loop without flyctl or the analyse layer present.
    """
    sources = built_sources if built_sources is not None else build_sources(config)
    if not sources:
        raise DispatcherError(
            "no sources configured; add at least one [[sources]] entry to paperbark.toml"
        )
    monitor_cfg = monitor if monitor is not None else config.monitor
    stop = stop_event if stop_event is not None else threading.Event()
    wall = clock if clock is not None else (lambda: datetime.now(tz=UTC))

    slug = _resolve_run_slug(monitor_cfg, rng)
    suffix = settings_suffix(monitor_cfg.interval, monitor_cfg.iterations)
    full_slug = f"{slug}_{suffix}"
    run_dir = new_run_dir(config.root, slug=full_slug, now=wall())
    monitor_log = run_dir / "monitor.log"

    def _log(message: str) -> None:
        # Append-only so a crash mid-run doesn't truncate prior history.
        with monitor_log.open("a", encoding="utf-8") as f:
            f.write(f"[{_iso_log_ts(wall())}] {message}\n")

    _log(f"Run dir: {run_dir}")
    _log(
        f"Sources: {', '.join(name for name, _ in sources)}; "
        f"interval={monitor_cfg.interval}s iterations={monitor_cfg.iterations} "
        f"analyse_every={monitor_cfg.analyse_every}s"
    )

    iteration = 0
    captured_total = 0
    start_mono = monotonic()
    last_snapshot_mono = start_mono
    snapshots_enabled = monitor_cfg.analyse_every > 0
    stopped_early = False

    def _publish(*, finished: bool) -> None:
        if on_state is None:
            return
        if snapshots_enabled:
            since = int(monotonic() - last_snapshot_mono)
            next_snapshot = max(monitor_cfg.analyse_every - since, 0)
        else:
            next_snapshot = -1
        on_state(
            MonitorState(
                iteration=iteration,
                iterations_max=monitor_cfg.iterations,
                elapsed_seconds=int(monotonic() - start_mono),
                captured_total=captured_total,
                next_snapshot_seconds=next_snapshot,
                finished=finished,
            )
        )

    _publish(finished=False)
    try:
        while not stop.is_set():
            iteration += 1
            iter_start_mono = monotonic()
            now_wall = wall()
            _log(f"Iteration {iteration}: capturing")
            run_iteration(sources, run_dir, iteration=iteration, now=now_wall)

            iter_lines = 0
            iter_ts = now_wall.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
            iter_label = f"iter_{iteration:04d}_{iter_ts}.log"
            for name, _src in sources:
                iter_lines += _count_lines(run_dir / name / "raw" / iter_label)
            captured_total += iter_lines
            _log(f"Iteration {iteration}: captured {iter_lines} new line(s)")

            if snapshots_enabled and (
                monotonic() - last_snapshot_mono >= monitor_cfg.analyse_every
            ):
                snap_ts = now_wall.astimezone(UTC).strftime("%H%M%SZ")
                snap_base = run_dir / "snapshots" / f"analysis_{snap_ts}"
                _log(f"Snapshot analyse → {snap_base}")
                if snapshot_runner is not None:
                    # One bad snapshot must not abort the loop; the bash
                    # dispatcher swallows analyse failures the same way.
                    try:
                        snapshot_runner(run_dir, snap_base)
                    except Exception as exc:
                        _log(f"Snapshot analyse failed: {exc}")
                last_snapshot_mono = monotonic()

            _publish(finished=False)

            if stop.is_set():
                stopped_early = True
                break
            if monitor_cfg.iterations > 0 and iteration >= monitor_cfg.iterations:
                break

            elapsed = monotonic() - iter_start_mono
            remaining = monitor_cfg.interval - elapsed
            if remaining > 0:
                # ``Event.wait`` returns True if the flag was set during the wait,
                # so we exit the sleep early on Ctrl+C without burning the full
                # interval. Returning False just means the timeout elapsed.
                if stop.wait(timeout=remaining):
                    stopped_early = True
                    break
            else:
                _log(
                    f"Iteration {iteration} took {elapsed:.1f}s "
                    f"(>= interval {monitor_cfg.interval}s); running back-to-back"
                )

        # Final analyse runs whether we stopped early or hit the iteration cap;
        # without it a Ctrl+C run would leave no analysis.{json,md} at the run
        # root and downstream tooling would have to re-run analyse manually.
        _log("Final analyse")
        if snapshot_runner is not None:
            try:
                snapshot_runner(run_dir, None)
            except Exception as exc:
                _log(f"Final analyse failed: {exc}")
    finally:
        _publish(finished=True)
        _log(f"Done after {iteration} iteration(s); captured {captured_total} new line(s)")

    return MonitorResult(
        run_dir=run_dir,
        iterations_completed=iteration,
        captured_total=captured_total,
        stopped_early=stopped_early,
    )
