"""Tests for paperbark.dispatcher."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paperbark.config import Config, ProbesConfig, SourceConfig
from paperbark.dispatcher import (
    DispatcherError,
    build_source,
    build_sources,
    capture_iteration,
    new_run_dir,
    run_iteration,
    run_monitor,
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


# --- new_run_dir -----------------------------------------------------------


def test_new_run_dir_creates_layout_under_root(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 3, 14, 30)
    run_dir = new_run_dir(tmp_path / "logs", now=fixed)
    assert run_dir == tmp_path / "logs" / "20260503" / "1430"
    assert run_dir.is_dir()


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
    run_dir = tmp_path / "20260503" / "1430"
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
    assert run_dir == tmp_path / "logs" / "20260503" / "1430"
    assert (run_dir / "api" / "summary.md").exists()


def test_run_monitor_raises_when_no_sources_configured(tmp_path: Path) -> None:
    config = Config(root=tmp_path)
    with pytest.raises(DispatcherError, match="no sources configured"):
        run_monitor(config)
