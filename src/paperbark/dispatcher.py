"""Dispatcher: compose sources → cursor filter → iteration summary → aggregate.

This module wires the per-layer pieces together so ``paperbark monitor``
runs end to end. The unit of work is one *iteration*: each configured
source captures a fresh window, the cursor filter dedupes against the
previous iteration's output, the surviving lines are written to disk and
summarised, and the per-app aggregate state is refreshed.

A run-dir laid out per :data:`docs/ROADMAP.md`'s public-contract section
is created on the first call and reused for every subsequent iteration::

    logs/YYYYMMDD/HHMM_<slug>_<settings>/
    ├── <app>/raw/<YYYYMMDDTHHMMSSZ>_iter<N>.log
    ├── <app>/.cursor
    ├── <app>/<YYYYMMDDTHHMMSSZ>_iter<N>.json
    ├── <app>/<YYYYMMDDTHHMMSSZ>_iter<N>.csv  # flat per-line side-output
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from paperbark.aggregate import aggregate
from paperbark.config import Config, MonitorConfig, SourceConfig
from paperbark.cursor import filter_stream
from paperbark.formats import Format, registered_formats
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
from paperbark.sources.flyctl import DEFAULT_SAMPLES as DEFAULT_FLYCTL_SAMPLES

# Type aliases for the loop's injection points. Keeping them at module scope
# avoids long inline ``Callable[...]`` annotations on every signature.
StateCallback = Callable[["MonitorState"], None]
StartCallback = Callable[["MonitorStart"], None]
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


_FORMAT_KEY_FIELDS: frozenset[str] = frozenset({"timestamp", "level", "message", "component"})

_JSON_FORMAT_NAME = "json"


def _resolve_format(raw: object, source_name: str) -> Format | None:
    """Look up a ``[[sources]].format`` preset name in the format registry.

    Returns ``None`` when the operator didn't supply ``format`` or supplied
    the explicit ``"json"`` sentinel (the default JSON-keys path is what the
    iteration parser already runs when no format is attached, so we don't
    need to attach an instance for it). Any other name must resolve against
    :func:`paperbark.formats.registered_formats` or we raise so a typo
    fails closed instead of silently dropping back to JSON.
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise DispatcherError(
            f"source {source_name!r}: 'format' must be a non-empty string, got {type(raw).__name__}"
        )
    if raw == _JSON_FORMAT_NAME:
        return None
    registry = registered_formats()
    if raw not in registry:
        joined = ", ".join(sorted(registry))
        raise DispatcherError(
            f"source {source_name!r}: unknown format {raw!r}; known presets: {joined}"
        )
    return registry[raw]


def _parse_format_keys(raw: object, source_name: str) -> dict[str, tuple[str, ...]] | None:
    """Validate a ``[[sources]].format_keys`` table and convert to canonical form.

    Returns ``None`` when the operator didn't supply any overrides — that is
    the loader's signal to ``iteration.summarise_lines`` that defaults apply.
    Each value may be a single string (sugar for a one-element list) or a
    list of strings; unknown field names are rejected so a typo can't
    silently disable detection of a canonical field.
    """
    if raw is None:
        return None
    from collections.abc import Mapping

    if not isinstance(raw, Mapping):
        raise DispatcherError(
            f"source {source_name!r}: 'format_keys' must be a table of "
            f"<field-name> = <key list>, got {type(raw).__name__}"
        )
    unknown = sorted(set(raw) - _FORMAT_KEY_FIELDS)
    if unknown:
        joined = ", ".join(repr(k) for k in unknown)
        allowed = ", ".join(repr(k) for k in sorted(_FORMAT_KEY_FIELDS))
        raise DispatcherError(
            f"source {source_name!r}: unknown format_keys field(s) {joined};"
            f" allowed fields: {allowed}"
        )
    out: dict[str, tuple[str, ...]] = {}
    for field_name, value in raw.items():
        if isinstance(value, str):
            keys: tuple[str, ...] = (value,)
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            keys = tuple(value)
        else:
            raise DispatcherError(
                f"source {source_name!r}: format_keys.{field_name} must be a"
                f" string or a list of strings"
            )
        if not keys or any(not k for k in keys):
            raise DispatcherError(
                f"source {source_name!r}: format_keys.{field_name} must"
                f" contain at least one non-empty key"
            )
        out[field_name] = keys
    return out


def _reject_unknown_options(spec: SourceConfig, allowed: frozenset[str]) -> None:
    """Fail closed on options the source type doesn't recognise.

    A typo in a TOML option key would otherwise be a silent no-op — the
    misspelled value never reaches the source constructor and the user
    sees no signal that the option was ignored.
    """
    unknown = sorted(set(spec.options) - allowed)
    if unknown:
        joined = ", ".join(repr(k) for k in unknown)
        raise DispatcherError(
            f"source {spec.name!r}: unknown option(s) {joined} for type {spec.type!r}"
        )


def build_source(spec: SourceConfig) -> Source:
    """Build a :class:`Source` from a parsed :class:`SourceConfig`.

    Raises :class:`DispatcherError` for unknown types, missing required
    options, or unrecognised option keys. The flyctl source is the only
    one currently usable; the stubs return Protocol-conformant instances
    so ``paperbark init`` / config validation can still resolve them.
    """
    if spec.type == "flyctl":
        _reject_unknown_options(
            spec, frozenset({"app", "no_tail", "samples", "format", "format_keys"})
        )
        app = spec.options.get("app")
        if not isinstance(app, str) or not app:
            raise DispatcherError(f"source {spec.name!r}: 'app' is required for flyctl")
        no_tail = bool(spec.options.get("no_tail", True))
        samples_raw = spec.options.get("samples", DEFAULT_FLYCTL_SAMPLES)
        if isinstance(samples_raw, bool) or not isinstance(samples_raw, int):
            # bool is an int subclass; reject so ``samples = true`` fails
            # closed instead of being read as 1.
            raise DispatcherError(
                f"source {spec.name!r}: 'samples' must be an integer, "
                f"got {type(samples_raw).__name__}"
            )
        if samples_raw <= 0:
            raise DispatcherError(f"source {spec.name!r}: 'samples' must be > 0, got {samples_raw}")
        line_format = _resolve_format(spec.options.get("format"), spec.name)
        format_keys = _parse_format_keys(spec.options.get("format_keys"), spec.name)
        if line_format is not None and format_keys is not None:
            # The two knobs target different parsers — ``format_keys`` tunes
            # the JSON path, regex presets ignore it. Failing closed beats
            # silently dropping the operator's intent on the floor.
            raise DispatcherError(
                f"source {spec.name!r}: 'format_keys' is JSON-only and cannot be"
                f" combined with format = {spec.options.get('format')!r}"
            )
        return FlyctlSource(
            app=app,
            no_tail=no_tail,
            samples=samples_raw,
            format_keys=format_keys,
            line_format=line_format,
        )
    if spec.type == "wrangler":
        _reject_unknown_options(spec, frozenset())
        return WranglerSource()
    if spec.type == "kubectl":
        _reject_unknown_options(spec, frozenset())
        return KubectlSource()
    if spec.type == "cloudwatch":
        _reject_unknown_options(spec, frozenset())
        return CloudWatchSource()
    if spec.type == "file":
        _reject_unknown_options(spec, frozenset())
        return FileSource()
    if spec.type == "stdin":
        _reject_unknown_options(spec, frozenset())
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
    ``app_dir/raw/<HHMMSSZ>_iter<N>.log``. The raw log is then
    summarised into ``app_dir/<HHMMSSZ>_iter<N>.json`` (the same
    shape :func:`paperbark.aggregate.merge_iteration` consumes), with a
    sibling ``<HHMMSSZ>_iter<N>.csv`` carrying the flat per-line dump
    (timestamp/level/component/message/extras) for ad-hoc spreadsheet
    inspection.

    The naming matches ``reference/process_logs.py`` so downstream
    tooling that scans run dirs by iteration filename keeps working.

    Returns ``(raw_log_path, summary_json_path)``.
    """
    moment = now if now is not None else datetime.now(tz=UTC)
    timestamp = moment.strftime("%Y%m%dT%H%M%SZ")
    iteration_label = f"{timestamp}_iter{iteration}"

    raw_dir = app_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_log = raw_dir / f"{iteration_label}.log"
    summary_json = app_dir / f"{iteration_label}.json"
    summary_csv = app_dir / f"{iteration_label}.csv"
    cursor_path = app_dir / ".cursor"

    cursor = ""
    if cursor_path.exists():
        cursor = cursor_path.read_text(encoding="utf-8").strip()

    with raw_log.open("w", encoding="utf-8") as f:
        new_cursor = filter_stream(source.capture(), cursor, write=f.write)

    if new_cursor and new_cursor != cursor:
        cursor_path.write_text(new_cursor, encoding="utf-8")

    # Sources may attach a ``format_keys`` mapping that overrides the JSON
    # key tuples ``iteration`` consults — useful when a Fly app emits
    # structured logs with non-default field names. ``line_format`` opts
    # the source onto the regex/format layer instead, for non-JSON shapes
    # (Apache combined, RFC 5424 syslog, …). Falling back to ``None`` for
    # both attributes preserves the v0.1 default-JSON behaviour for sources
    # that don't carry them (the stub sources, for example).
    format_keys = getattr(source, "format_keys", None)
    line_format = getattr(source, "line_format", None)
    summary = summarise_log_file(
        raw_log,
        flat_csv_path=summary_csv,
        format_keys=format_keys,
        line_format=line_format,
    )
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


@dataclass(frozen=True)
class MonitorStart:
    """Run identifiers fired once before the loop begins.

    The CLI uses this to render the bash-parity startup banner (rule + Run /
    Sources / Interval / Iterations / Snapshots key-value rows) above the
    live ticker. Tests can assert on it without parsing terminal output.
    """

    run_dir: Path
    source_names: tuple[str, ...]
    monitor: MonitorConfig


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


def cleanup_old_runs(
    root: Path,
    *,
    days: int,
    mode: str,
    log: Callable[[str], None] | None = None,
    today: datetime | None = None,
) -> None:
    """Rotate run dirs older than ``days`` under ``root``.

    Mirrors the bash dispatcher's ``perform_cleanup`` step: any run dir under
    ``<root>/<YYYYMMDD>/<run>`` whose date component is strictly less than
    ``today - days`` is processed. ``mode == "zip"`` archives every per-app
    ``raw/`` directory into a sibling ``raw.zip`` and removes the bulky
    per-iter ``*_iter*.{json,csv}`` files (summaries and aggregate CSVs are
    kept). ``mode == "delete"`` removes the run dir outright.

    No-op when ``root`` doesn't exist yet (first-run case). Failures on
    individual runs are logged and swallowed so a single bad dir can't abort
    the rest of the rotation pass.

    ``today`` is injectable for tests so the cutoff is deterministic.
    """
    if mode not in ("zip", "delete"):
        raise ValueError(f"cleanup mode must be 'zip' or 'delete', got {mode!r}")
    if days < 0:
        raise ValueError(f"cleanup days must be >= 0, got {days}")
    if not root.exists():
        return
    log_fn = log or (lambda _msg: None)
    moment = today if today is not None else datetime.now(tz=UTC)
    cutoff = (moment.date() - timedelta(days=days)).strftime("%Y%m%d")
    log_fn(f"Cleanup pass: cutoff {cutoff} (older than {days} day(s), mode {mode})")
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir() or len(date_dir.name) != 8 or not date_dir.name.isdigit():
            continue
        if date_dir.name >= cutoff:
            continue
        for run_dir in sorted(date_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            try:
                if mode == "zip":
                    _zip_rotate_run(run_dir, log_fn)
                else:
                    _delete_run(run_dir, log_fn)
            except OSError as exc:  # pragma: no cover — defensive logging
                log_fn(f"  Cleanup error on {run_dir}: {exc}")


def _zip_rotate_run(run_dir: Path, log: Callable[[str], None]) -> None:
    """Implement ``mode == "zip"`` for one run dir.

    For every ``<app>/raw/`` directory found, write a sibling ``raw.zip`` and
    remove the original tree; existing ``raw.zip`` files are left alone so
    re-running cleanup is idempotent. Per-iter ``*_iter*.{json,csv}`` files
    are then removed across the whole run, matching the bash dispatcher's
    intent: keep ``summary.md`` / time-series CSVs, drop the bulky raw and
    iteration-level artefacts.
    """
    import shutil
    import zipfile

    rel = run_dir.parent.name + "/" + run_dir.name
    for raw_dir in sorted(run_dir.glob("*/raw")):
        if not raw_dir.is_dir():
            continue
        zip_path = raw_dir.parent / "raw.zip"
        if zip_path.exists():
            continue
        log(f"  Zipping raw logs: {rel}/{raw_dir.parent.name}/raw")
        # ``shutil.make_archive`` writes ``<base>.zip`` — point ``base`` at the
        # final path minus the suffix so we land on ``raw.zip`` next to the
        # source dir, then verify the archive is readable before removing the
        # original tree. ``testzip()`` returns the name of the first bad
        # member or ``None`` if every entry's CRC checks out.
        base = str(raw_dir.parent / "raw")
        try:
            shutil.make_archive(base, "zip", root_dir=raw_dir.parent, base_dir="raw")
        except OSError as exc:
            log(f"  Failed to zip {raw_dir}: {exc}")
            continue
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                bad_member = zf.testzip()
        except (zipfile.BadZipFile, OSError) as exc:
            log(f"  Archive verification failed for {zip_path} ({exc}); keeping {raw_dir}")
            zip_path.unlink(missing_ok=True)
            continue
        if bad_member is not None:
            log(
                f"  Archive verification failed for {zip_path} "
                f"(corrupt member {bad_member!r}); keeping {raw_dir}"
            )
            zip_path.unlink(missing_ok=True)
            continue
        shutil.rmtree(raw_dir)
    # The glob accepts the bash dispatcher's ``<TS>_iter<N>`` shape. Both
    # extensions are removed because v0.1.1 reintroduces the per-iter CSV
    # alongside the JSON; bash only had the JSON to clean up.
    iter_files = list(run_dir.glob("*/*_iter*.json")) + list(run_dir.glob("*/*_iter*.csv"))
    if iter_files:
        log(f"  Removing {len(iter_files)} iteration file(s): {rel}")
        for path in iter_files:
            try:
                path.unlink()
            except OSError as exc:  # pragma: no cover — defensive logging
                log(f"  Failed to remove {path}: {exc}")


def _delete_run(run_dir: Path, log: Callable[[str], None]) -> None:
    import shutil

    rel = run_dir.parent.name + "/" + run_dir.name
    log(f"  Deleting: {rel}")
    shutil.rmtree(run_dir, ignore_errors=False)


# Parse-rate threshold below which we record a format-mismatch hint to
# ``monitor.log`` for post-mortem diagnosis. We deliberately do *not* surface
# this on stderr — many healthy sources mix structured records with plain
# keepalives, banners, or platform notices, so a low parse rate is a weak
# signal in isolation. The bash reference never warned either; the line
# stays in the run log so a real silent-failure case can still be traced.
_PARSE_WARN_MIN_LINES = 5
_PARSE_WARN_RATE = 0.5


def _maybe_log_parse_rate(
    *,
    name: str,
    summary_path: Path,
    log: Callable[[str], None],
) -> None:
    """Append a format-mismatch hint to ``monitor.log`` when parse rate is low.

    Silent on stderr by design — the CLI used to emit a one-time warning per
    source but it false-positived on healthy mixed-format sources. The
    monitor.log line is enough to investigate when probes downstream surface
    a depleted record set.
    """
    if not summary_path.exists():
        return
    try:
        meta = json.loads(summary_path.read_text(encoding="utf-8")).get("meta", {})
    except (OSError, json.JSONDecodeError):
        return
    total = int(meta.get("total_lines", 0) or 0)
    parsed = int(meta.get("parsed", 0) or 0)
    if total < _PARSE_WARN_MIN_LINES:
        return
    rate = parsed / total
    if rate > _PARSE_WARN_RATE:
        return
    pct = int(rate * 100)
    log(
        f"Source {name!r}: {parsed}/{total} lines parsed ({pct}%) — format may "
        f"not match the captured log shape; probes downstream will see a "
        f"depleted record set."
    )


def run_monitor_loop(
    config: Config,
    *,
    monitor: MonitorConfig | None = None,
    built_sources: Sequence[tuple[str, Source]] | None = None,
    stop_event: threading.Event | None = None,
    on_start: StartCallback | None = None,
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

    if monitor_cfg.cleanup_enabled:
        # Rotate first so the new run dir we just created can never collide
        # with a stale archive of the same name; cleanup_old_runs uses the
        # date directory as its cutoff, so today's runs are always safe.
        cleanup_old_runs(
            config.root,
            days=monitor_cfg.cleanup_days,
            mode=monitor_cfg.cleanup_mode,
            log=_log,
            today=wall(),
        )
    _log(f"Run dir: {run_dir}")
    _log(
        f"Sources: {', '.join(name for name, _ in sources)}; "
        f"interval={monitor_cfg.interval}s iterations={monitor_cfg.iterations} "
        f"analyse_every={monitor_cfg.analyse_every}s"
    )

    if on_start is not None:
        on_start(
            MonitorStart(
                run_dir=run_dir,
                source_names=tuple(name for name, _ in sources),
                monitor=monitor_cfg,
            )
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
            iter_label_base = f"{iter_ts}_iter{iteration}"
            for name, _src in sources:
                app_dir = run_dir / name
                iter_lines += _count_lines(app_dir / "raw" / f"{iter_label_base}.log")
                _maybe_log_parse_rate(
                    name=name,
                    summary_path=app_dir / f"{iter_label_base}.json",
                    log=_log,
                )
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
