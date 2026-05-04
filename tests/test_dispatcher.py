"""Tests for paperbark.dispatcher."""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paperbark.config import Config, MonitorConfig, ProbesConfig, SourceConfig
from paperbark.dispatcher import (
    DispatcherError,
    MonitorResult,
    MonitorState,
    build_source,
    build_sources,
    capture_iteration,
    new_run_dir,
    random_slug,
    run_iteration,
    run_monitor,
    run_monitor_loop,
    settings_suffix,
)
from paperbark.sources import (
    CloudWatchSource,
    FileSource,
    FlyctlSource,
    KubectlSource,
    Source,
    StdinSource,
    WranglerSource,
)

# --- build_source ----------------------------------------------------------


def test_build_source_returns_flyctl_with_options() -> None:
    spec = SourceConfig(name="main", type="flyctl", options={"app": "fly-a", "no_tail": True})
    source = build_source(spec)
    assert isinstance(source, FlyctlSource)
    assert source.app == "fly-a"
    assert source.no_tail is True


def test_build_source_flyctl_defaults_no_tail_true() -> None:
    spec = SourceConfig(name="main", type="flyctl", options={"app": "fly-a"})
    source = build_source(spec)
    assert isinstance(source, FlyctlSource)
    assert source.no_tail is True


def test_build_source_flyctl_requires_app() -> None:
    spec = SourceConfig(name="main", type="flyctl", options={})
    with pytest.raises(DispatcherError, match="'app' is required"):
        build_source(spec)


def test_build_source_rejects_unknown_type() -> None:
    spec = SourceConfig(name="weird", type="banana", options={})
    with pytest.raises(DispatcherError, match="unknown type"):
        build_source(spec)


@pytest.mark.parametrize(
    "type_, expected_class",
    [
        ("wrangler", WranglerSource),
        ("kubectl", KubectlSource),
        ("cloudwatch", CloudWatchSource),
        ("file", FileSource),
        ("stdin", StdinSource),
    ],
)
def test_build_source_returns_stub_classes(type_: str, expected_class: type[Source]) -> None:
    spec = SourceConfig(name="x", type=type_, options={})
    assert isinstance(build_source(spec), expected_class)


def test_build_sources_preserves_order_and_names() -> None:
    config = Config(
        sources=(
            SourceConfig(name="first", type="flyctl", options={"app": "a"}),
            SourceConfig(name="second", type="flyctl", options={"app": "b"}),
        ),
    )
    built = build_sources(config)
    assert [name for name, _ in built] == ["first", "second"]


# --- random_slug -----------------------------------------------------------


def test_random_slug_is_adjective_colour_form() -> None:
    slug = random_slug(rng=random.Random(42))  # noqa: S311
    assert slug.count("-") == 1
    adjective, colour = slug.split("-")
    assert adjective.isalpha() and colour.isalpha()


def test_random_slug_is_deterministic_with_seeded_rng() -> None:
    # Same seed → same slug. Tests downstream of `random_slug` rely on this to
    # avoid flaky string assertions.
    a = random_slug(rng=random.Random(123))  # noqa: S311
    b = random_slug(rng=random.Random(123))  # noqa: S311
    assert a == b


def test_random_slug_default_rng_runs_without_args() -> None:
    # Smoke check: no-arg form picks an OS-seeded RNG and returns a valid slug.
    slug = random_slug()
    assert slug.count("-") == 1


# --- settings_suffix -------------------------------------------------------


@pytest.mark.parametrize(
    "interval, iterations, expected",
    [
        # Bash defaults: 3s * 1440 = 4320s; (4320+1800)//3600 = 1 -> "1h".
        (3, 1440, "3s_1h"),
        (3, 0, "3s_forever"),
        # < 3600: minute branch with round-half-up via +30 nudge.
        (3, 300, "3s_15m"),
        (3, 600, "3s_30m"),
        (3, 1100, "3s_55m"),  # 3300s → 55m exact
        (60, 30, "1m_30m"),
        # >= 3600 boundary: hour branch.
        (3, 1200, "3s_1h"),  # 3600s → 1h exact
        (5, 720, "5s_1h"),
        (3, 3600, "3s_3h"),  # 10 800s → 3h exact
        # >= 86400: day branch.
        (60, 60 * 24, "1m_1d"),  # 86 400s → 1d exact
        (3600, 24, "60m_1d"),
        (3, 60 * 60 * 24, "3s_3d"),  # 3s * 86_400 = 259_200s = 3 days exact
    ],
)
def test_settings_suffix_matches_bash(interval: int, iterations: int, expected: str) -> None:
    assert settings_suffix(interval, iterations) == expected


def test_settings_suffix_rejects_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="interval must be > 0"):
        settings_suffix(0, 100)


def test_settings_suffix_rejects_negative_iterations() -> None:
    with pytest.raises(ValueError, match="iterations must be >= 0"):
        settings_suffix(3, -1)


# --- new_run_dir -----------------------------------------------------------


def test_new_run_dir_creates_layout_under_root(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30)
    run_dir = new_run_dir(tmp_path / "logs", now=fixed)
    # Default slug "run" — required so search.resolve_runs can discover the dir.
    assert run_dir == tmp_path / "logs" / "20260503" / "1430_run"
    assert run_dir.is_dir()


def test_new_run_dir_uses_supplied_slug(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30)
    run_dir = new_run_dir(tmp_path, slug="api_worker", now=fixed)
    assert run_dir.name == "1430_api_worker"


def test_new_run_dir_sanitises_unsafe_slug_chars(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30)
    run_dir = new_run_dir(tmp_path, slug="hello world / friend", now=fixed)
    # Spaces and slashes become hyphens; consecutive separators preserved as-is.
    assert run_dir.name == "1430_hello-world---friend"


def test_new_run_dir_falls_back_when_slug_is_empty(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30)
    run_dir = new_run_dir(tmp_path, slug="", now=fixed)
    assert run_dir.name == "1430_run"


def test_new_run_dir_is_idempotent(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30)
    first = new_run_dir(tmp_path, now=fixed)
    second = new_run_dir(tmp_path, now=fixed)
    assert first == second


# --- capture_iteration -----------------------------------------------------


class _FakeSource:
    """Test double that yields scripted lines and tracks call count."""

    name = "fake"

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.calls = 0

    def capture(self, *, since: str = "") -> Iterator[str]:
        self.calls += 1
        yield from self.lines


def _scripted_lines() -> list[str]:
    return [
        '2026-05-03T02:00:01Z {"time":"2026-05-03T02:00:01Z","level":"info","msg":"served"}\n',
        '2026-05-03T02:00:02Z {"time":"2026-05-03T02:00:02Z","level":"warn","msg":"slow"}\n',
    ]


def test_capture_iteration_writes_raw_log_and_summary(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    source = _FakeSource(_scripted_lines())
    raw_log, summary_json = capture_iteration(source, tmp_path / "app", 1, now=fixed)
    assert raw_log.exists()
    assert summary_json.exists()
    assert raw_log.parent.name == "raw"
    assert raw_log.name == "iter_0001_20260503T143045Z.log"
    assert summary_json.name == "iter_0001_20260503T143045Z.json"
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["meta"]["parsed"] == 2


def test_capture_iteration_writes_cursor_file(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    source = _FakeSource(_scripted_lines())
    capture_iteration(source, tmp_path / "app", 1, now=fixed)
    cursor = (tmp_path / "app" / ".cursor").read_text(encoding="utf-8")
    assert cursor == "2026-05-03T02:00:02+00:00"


def test_capture_iteration_dedupes_against_existing_cursor(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / ".cursor").write_text("2026-05-03T02:00:01+00:00", encoding="utf-8")
    source = _FakeSource(_scripted_lines())
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    raw_log, _ = capture_iteration(source, app_dir, 2, now=fixed)
    # First line is at the cursor and should be dropped.
    body = raw_log.read_text(encoding="utf-8")
    assert "served" not in body
    assert "slow" in body


def test_capture_iteration_does_not_rewrite_unchanged_cursor(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    cursor_path = app_dir / ".cursor"
    cursor_path.write_text("2026-05-03T05:00:00+00:00", encoding="utf-8")
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    # Source emits only stale lines; the cursor must not advance.
    source = _FakeSource(_scripted_lines())
    capture_iteration(source, app_dir, 3, now=fixed)
    assert cursor_path.read_text(encoding="utf-8") == "2026-05-03T05:00:00+00:00"


# --- run_iteration ---------------------------------------------------------


def test_run_iteration_creates_per_app_dirs_and_aggregates(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    sources = [
        ("api", _FakeSource(_scripted_lines())),
        ("worker", _FakeSource(_scripted_lines())),
    ]
    run_dir = tmp_path / "20260503" / "1430_test"
    run_dir.mkdir(parents=True)
    run_iteration(sources, run_dir, iteration=1, now=fixed)
    for app in ("api", "worker"):
        app_dir = run_dir / app
        assert (app_dir / "raw" / "iter_0001_20260503T143045Z.log").exists()
        assert (app_dir / "iter_0001_20260503T143045Z.json").exists()
        # aggregate() output:
        assert (app_dir / "time_series.csv").exists()
        assert (app_dir / "summary.md").exists()


# --- run_monitor -----------------------------------------------------------


def test_run_monitor_returns_run_dir_path(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    config = Config(
        root=tmp_path / "logs",
        sources=(SourceConfig(name="api", type="flyctl", options={"app": "fly-a"}),),
        probes=ProbesConfig(),
    )
    run_dir = run_monitor(
        config,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        now=fixed,
    )
    # Slug derived from source name, matching the public run-dir contract.
    assert run_dir == tmp_path / "logs" / "20260503" / "1430_api"
    assert (run_dir / "api" / "summary.md").exists()


def test_run_monitor_derives_slug_from_multiple_source_names(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    config = Config(root=tmp_path / "logs")
    run_dir = run_monitor(
        config,
        built_sources=[
            ("api", _FakeSource(_scripted_lines())),
            ("worker", _FakeSource(_scripted_lines())),
        ],
        now=fixed,
    )
    assert run_dir.name == "1430_api_worker"


def test_run_monitor_raises_when_no_sources_configured(tmp_path: Path) -> None:
    config = Config(root=tmp_path)
    with pytest.raises(DispatcherError, match="no sources configured"):
        run_monitor(config)


def test_run_monitor_raises_when_built_sources_is_empty(tmp_path: Path) -> None:
    # Explicit empty injection bypasses the config.sources path; the post-resolution
    # guard must still fail closed rather than silently creating an empty run dir.
    config = Config(
        root=tmp_path / "logs",
        sources=(SourceConfig(name="api", type="flyctl", options={"app": "fly-a"}),),
    )
    with pytest.raises(DispatcherError, match="no sources configured"):
        run_monitor(config, built_sources=[])


# --- run_monitor_loop ------------------------------------------------------


class _FakeMonotonic:
    """Test double for ``time.monotonic`` that advances on demand.

    Each call returns the current value. ``advance(seconds)`` jumps it forward
    so the loop's elapsed/sleep arithmetic works without real sleeps.
    """

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _build_monitor_config(
    *,
    interval: int = 1,
    iterations: int = 0,
    analyse_every: int = 0,
    run_id: str = "test",
) -> MonitorConfig:
    return MonitorConfig(
        interval=interval,
        iterations=iterations,
        analyse_every=analyse_every,
        run_id=run_id,
    )


def test_run_monitor_loop_runs_n_iterations_when_capped(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    sources = [("api", _FakeSource(_scripted_lines()))]
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=1, iterations=2, analyse_every=0)
    mono = _FakeMonotonic()
    stop = threading.Event()

    states: list[MonitorState] = []

    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=sources,
        stop_event=stop,
        on_state=states.append,
        monotonic=mono,
        clock=lambda: fixed,
    )
    assert isinstance(result, MonitorResult)
    assert result.iterations_completed == 2
    assert result.stopped_early is False
    # Expected state pushes: initial (iteration=0), one per iteration (1, 2),
    # and the final finished=True publish — four total.
    iter_values = [s.iteration for s in states]
    assert iter_values == [0, 1, 2, 2]
    assert states[-1].finished is True
    assert states[-1].next_snapshot_seconds == -1  # snapshots disabled


def test_run_monitor_loop_stops_when_stop_event_set(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=1, iterations=0, analyse_every=0)
    mono = _FakeMonotonic()
    stop = threading.Event()

    states: list[MonitorState] = []

    def _stop_after_first(state: MonitorState) -> None:
        states.append(state)
        if state.iteration == 1:
            stop.set()

    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=stop,
        on_state=_stop_after_first,
        monotonic=mono,
        clock=lambda: fixed,
    )
    assert result.iterations_completed == 1
    assert result.stopped_early is True


def test_run_monitor_loop_invokes_snapshot_runner_at_cadence(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=1, iterations=3, analyse_every=10)
    mono = _FakeMonotonic()
    stop = threading.Event()
    snapshot_calls: list[tuple[Path, Path | None]] = []

    def _snapshot(run_dir: Path, out: Path | None) -> None:
        snapshot_calls.append((run_dir, out))

    # Advance clock by 11s after each iteration via on_state, ensuring the
    # snapshot threshold (10s) is crossed every iteration.
    def _on_state(_state: MonitorState) -> None:
        mono.advance(11)

    run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=stop,
        on_state=_on_state,
        snapshot_runner=_snapshot,
        monotonic=mono,
        clock=lambda: fixed,
    )
    # 3 per-iteration snapshots + 1 final analyse (out=None).
    assert len(snapshot_calls) == 4
    final_call = snapshot_calls[-1]
    assert final_call[1] is None
    snapshot_outs = [call[1] for call in snapshot_calls[:-1]]
    assert all(out is not None for out in snapshot_outs)
    assert all(out is not None and out.parent.name == "snapshots" for out in snapshot_outs)


def test_run_monitor_loop_skips_snapshots_when_disabled(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=1, iterations=2, analyse_every=0)
    snapshot_calls: list[tuple[Path, Path | None]] = []

    run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=threading.Event(),
        snapshot_runner=lambda rd, out: snapshot_calls.append((rd, out)),
        monotonic=_FakeMonotonic(),
        clock=lambda: fixed,
    )
    # Only the final analyse should fire — no per-iteration snapshots.
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0][1] is None


def test_run_monitor_loop_writes_monitor_log(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=1, iterations=1, analyse_every=0)

    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=threading.Event(),
        monotonic=_FakeMonotonic(),
        clock=lambda: fixed,
    )
    log_text = (result.run_dir / "monitor.log").read_text(encoding="utf-8")
    assert "Run dir:" in log_text
    assert "Iteration 1: capturing" in log_text
    assert "Done after 1 iteration(s)" in log_text


def test_run_monitor_loop_run_dir_uses_settings_suffix(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    # ``iterations`` drives the suffix arithmetic, not the actual loop count;
    # we stop after the first iteration so the test runs instantly while
    # still proving that 1200 * 3s ~= 1h round-trips through ``settings_suffix``.
    monitor = _build_monitor_config(interval=3, iterations=1200, analyse_every=0, run_id="incident")
    stop = threading.Event()

    def _stop_after_first(state: MonitorState) -> None:
        if state.iteration == 1:
            stop.set()

    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=stop,
        on_state=_stop_after_first,
        monotonic=_FakeMonotonic(),
        clock=lambda: fixed,
    )
    assert result.run_dir.name == "1430_incident_3s_1h"


def test_run_monitor_loop_auto_generates_slug_when_run_id_blank(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=3, iterations=1, analyse_every=0, run_id="")

    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=threading.Event(),
        rng=random.Random(42),  # noqa: S311
        monotonic=_FakeMonotonic(),
        clock=lambda: fixed,
    )
    # Auto slug is ``<adjective>-<colour>``; suffix carries its own underscore
    # (``3s_0m``) so the full run-dir name has three underscore-separated
    # halves: time prefix, slug, and the two-part settings suffix.
    name = result.run_dir.name
    assert name.startswith("1430_")
    remainder = name[len("1430_") :]
    slug, _, suffix = remainder.partition("_")
    assert "-" in slug  # adjective-colour shape
    assert suffix == "3s_0m"


def test_run_monitor_loop_swallows_snapshot_runner_errors(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    monitor = _build_monitor_config(interval=1, iterations=1, analyse_every=0)

    def _broken(_run_dir: Path, _out: Path | None) -> None:
        raise RuntimeError("analyse blew up")

    # Final analyse fails but the loop must still complete and return a result.
    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=threading.Event(),
        snapshot_runner=_broken,
        monotonic=_FakeMonotonic(),
        clock=lambda: fixed,
    )
    assert result.iterations_completed == 1
    log_text = (result.run_dir / "monitor.log").read_text(encoding="utf-8")
    assert "Final analyse failed: analyse blew up" in log_text


def test_run_monitor_loop_logs_overrun_warning(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    cfg = Config(root=tmp_path / "logs", sources=(SourceConfig(name="api", type="flyctl"),))
    # Two iterations so the loop reaches the remaining-budget check after iter 1
    # (with iterations=1 the loop breaks before computing remaining).
    monitor = _build_monitor_config(interval=1, iterations=2, analyse_every=0)

    mono = _FakeMonotonic()

    # Advance the clock by more than ``interval`` between iter_start and the
    # post-iter remaining-budget check so the overrun branch triggers. on_state
    # fires before that check, so advancing here simulates a slow iteration.
    def _on_state(_s: MonitorState) -> None:
        mono.advance(5)

    result = run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("api", _FakeSource(_scripted_lines()))],
        stop_event=threading.Event(),
        on_state=_on_state,
        monotonic=mono,
        clock=lambda: fixed,
    )
    log_text = (result.run_dir / "monitor.log").read_text(encoding="utf-8")
    assert "running back-to-back" in log_text
