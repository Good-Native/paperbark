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


def test_build_source_flyctl_rejects_unknown_option() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "appp": "typo"},
    )
    with pytest.raises(DispatcherError, match=r"unknown option\(s\) 'appp' for type 'flyctl'"):
        build_source(spec)


def test_build_source_flyctl_lists_unknown_options_alphabetically() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "zebra": 1, "alpha": 2},
    )
    with pytest.raises(DispatcherError, match=r"unknown option\(s\) 'alpha', 'zebra'"):
        build_source(spec)


@pytest.mark.parametrize("type_", ["wrangler", "kubectl", "cloudwatch", "file", "stdin"])
def test_build_source_stub_rejects_unknown_option(type_: str) -> None:
    spec = SourceConfig(name="x", type=type_, options={"path": "/var/log/foo"})
    with pytest.raises(DispatcherError, match=r"unknown option\(s\) 'path'"):
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
    assert raw_log.name == "20260503T143045Z_iter1.log"
    assert summary_json.name == "20260503T143045Z_iter1.json"
    # The flat per-line CSV side-output lands next to the JSON; this is the
    # bash-parity restoration in v0.1.1.
    assert (raw_log.parent.parent / "20260503T143045Z_iter1.csv").exists()
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
        assert (app_dir / "raw" / "20260503T143045Z_iter1.log").exists()
        assert (app_dir / "20260503T143045Z_iter1.json").exists()
        assert (app_dir / "20260503T143045Z_iter1.csv").exists()
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


# --- v0.1.1: samples knob + format_keys override --------------------------


def test_build_source_flyctl_threads_samples_through() -> None:
    """``samples`` is enforced inside ``capture()`` via a bounded deque (the
    bash dispatcher's ``| tail -n <samples>`` analogue), not via a flyctl
    flag. The attribute round-trips so a probe of ``source.samples`` confirms
    the TOML value reached the source instance.
    """
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "samples": 750},
    )
    source = build_source(spec)
    assert isinstance(source, FlyctlSource)
    assert source.samples == 750
    # Sanity-check the slicing: a runner that yields more than ``samples``
    # lines should be trimmed at capture time.
    source._runner = lambda _cmd: iter(f"line {i}\n" for i in range(800))
    assert len(list(source.capture())) == 750


def test_build_source_flyctl_rejects_non_int_samples() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "samples": "lots"},
    )
    with pytest.raises(DispatcherError, match="'samples' must be an integer"):
        build_source(spec)


def test_build_source_flyctl_rejects_zero_samples() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "samples": 0},
    )
    with pytest.raises(DispatcherError, match="'samples' must be > 0"):
        build_source(spec)


def test_build_source_flyctl_rejects_bool_samples() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "samples": True},
    )
    with pytest.raises(DispatcherError, match="'samples' must be an integer"):
        build_source(spec)


def test_build_source_flyctl_accepts_format_keys_table() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={
            "app": "fly-a",
            "format_keys": {"timestamp": "ts", "level": ["severity", "lvl"]},
        },
    )
    source = build_source(spec)
    assert isinstance(source, FlyctlSource)
    assert source.format_keys == {"timestamp": ("ts",), "level": ("severity", "lvl")}


def test_build_source_flyctl_rejects_unknown_format_keys_field() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "format_keys": {"timestamp": "ts", "tier": "x"}},
    )
    with pytest.raises(DispatcherError, match="unknown format_keys field"):
        build_source(spec)


def test_build_source_flyctl_rejects_non_string_format_keys_value() -> None:
    spec = SourceConfig(
        name="main",
        type="flyctl",
        options={"app": "fly-a", "format_keys": {"timestamp": 123}},
    )
    with pytest.raises(DispatcherError, match="must be a string or a list of strings"):
        build_source(spec)


def test_capture_iteration_uses_source_format_keys(tmp_path: Path) -> None:
    """The dispatcher reads ``source.format_keys`` and threads it through.

    A line whose timestamp/level/message live under non-default keys would
    otherwise show up as ``unknown`` everywhere; with overrides set the
    parsed counts should match a Fly-style line.
    """
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    nondefault_lines = [
        '2026-05-03T02:00:01Z {"ts":"2026-05-03T02:00:01Z","lvl":"INFO",'
        '"text":"hello","service":"api"}\n',
    ]
    source = FlyctlSource(
        app="example",
        runner=lambda _cmd: iter(nondefault_lines),
        format_keys={
            "timestamp": ("ts",),
            "level": ("lvl",),
            "message": ("text",),
            "component": ("service",),
        },
    )
    raw_log, summary_json = capture_iteration(source, tmp_path / "app", 1, now=fixed)
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["meta"]["parsed"] == 1
    # Component bucket should pick up the overridden ``service`` key.
    component_counts = summary["component_counts"]
    assert "api" in next(iter(component_counts.values()))
    raw_log.unlink()


# --- v0.1.1: cleanup / rotation -------------------------------------------


def _seed_old_run(root: Path, date: str, run_name: str) -> Path:
    run_dir = root / date / run_name
    (run_dir / "app1" / "raw").mkdir(parents=True)
    (run_dir / "app1" / "raw" / "20260101T000000Z_iter1.log").write_text("line\n")
    (run_dir / "app1" / "20260101T000000Z_iter1.json").write_text("{}")
    (run_dir / "app1" / "20260101T000000Z_iter1.csv").write_text("h\n")
    (run_dir / "app1" / "summary.md").write_text("# kept\n")
    return run_dir


def test_cleanup_zip_archives_raw_and_strips_iter_files(tmp_path: Path) -> None:
    from paperbark.dispatcher import cleanup_old_runs

    today = datetime(2026, 5, 5, tzinfo=UTC)
    old = _seed_old_run(tmp_path / "logs", "20260101", "0900_old")
    cleanup_old_runs(tmp_path / "logs", days=1, mode="zip", today=today)
    # raw/ tree replaced by raw.zip
    assert not (old / "app1" / "raw").exists()
    assert (old / "app1" / "raw.zip").exists()
    # iter JSON + CSV stripped, summary kept.
    assert not (old / "app1" / "20260101T000000Z_iter1.json").exists()
    assert not (old / "app1" / "20260101T000000Z_iter1.csv").exists()
    assert (old / "app1" / "summary.md").exists()


def test_cleanup_delete_removes_run_dir(tmp_path: Path) -> None:
    from paperbark.dispatcher import cleanup_old_runs

    today = datetime(2026, 5, 5, tzinfo=UTC)
    old = _seed_old_run(tmp_path / "logs", "20260101", "0900_old")
    cleanup_old_runs(tmp_path / "logs", days=1, mode="delete", today=today)
    assert not old.exists()


def test_cleanup_keeps_runs_inside_window(tmp_path: Path) -> None:
    from paperbark.dispatcher import cleanup_old_runs

    today = datetime(2026, 5, 5, tzinfo=UTC)
    fresh = _seed_old_run(tmp_path / "logs", "20260505", "0900_today")
    yesterday = _seed_old_run(tmp_path / "logs", "20260504", "0900_yesterday")
    cleanup_old_runs(tmp_path / "logs", days=1, mode="delete", today=today)
    assert fresh.exists()
    assert yesterday.exists()


def test_cleanup_zip_is_idempotent(tmp_path: Path) -> None:
    from paperbark.dispatcher import cleanup_old_runs

    today = datetime(2026, 5, 5, tzinfo=UTC)
    old = _seed_old_run(tmp_path / "logs", "20260101", "0900_old")
    cleanup_old_runs(tmp_path / "logs", days=1, mode="zip", today=today)
    # Second pass must be a no-op once raw.zip exists; no error, no clobber.
    cleanup_old_runs(tmp_path / "logs", days=1, mode="zip", today=today)
    assert (old / "app1" / "raw.zip").exists()


def test_cleanup_no_op_when_root_missing(tmp_path: Path) -> None:
    from paperbark.dispatcher import cleanup_old_runs

    cleanup_old_runs(tmp_path / "missing", days=1, mode="zip")  # must not raise


def test_cleanup_rejects_invalid_mode(tmp_path: Path) -> None:
    from paperbark.dispatcher import cleanup_old_runs

    with pytest.raises(ValueError, match="cleanup mode must be"):
        cleanup_old_runs(tmp_path, days=1, mode="bogus")


# --- v0.1.1: parse-rate warning -------------------------------------------


def test_loop_warns_on_silent_format_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A source that captures lines but parses none should trip a warning.

    Use an iteration count of 1 so the loop runs end-to-end with predictable
    output. The line is plausibly a non-JSON shape (no embedded record)
    so ``iteration._try_parse_json_record`` returns ``None`` for every line.
    """
    fixed = datetime(2026, 5, 3, 14, 30, 45, tzinfo=UTC)
    plain_lines = [f"2026-05-03T02:00:0{i}Z plain text line without json\n" for i in range(6)]
    cfg = Config(root=tmp_path / "logs")
    monitor = MonitorConfig(interval=1, iterations=1, analyse_every=0, cleanup_enabled=False)
    mono_seq = iter([0.0, 0.1, 0.2, 0.3])
    run_monitor_loop(
        cfg,
        monitor=monitor,
        built_sources=[("nonjson", _FakeSource(plain_lines))],
        stop_event=threading.Event(),
        monotonic=lambda: next(mono_seq),
        clock=lambda: fixed,
    )
    err = capsys.readouterr().err
    assert "source 'nonjson'" in err
    assert "parsed 0/6 line(s)" in err
    assert "(0%)" in err


# Helper used by the parse-rate test — repurposed from earlier _FakeSource
# but lifted here so the new tests stay self-contained.
