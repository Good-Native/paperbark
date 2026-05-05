"""Tests for the CLI glue around ``paperbark monitor``.

Covers the argparse-to-MonitorConfig override path and the snapshot-runner
factory; the dispatcher loop itself is exercised in ``test_dispatcher.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from paperbark.cli import (
    _make_snapshot_runner,
    _merge_monitor_overrides,
    _print_state_line,
)
from paperbark.config import MonitorConfig, ProbesConfig
from paperbark.dispatcher import MonitorState


def _ns(**overrides: object) -> argparse.Namespace:
    """Build an argparse.Namespace with the monitor flag defaults.

    Mirrors what argparse produces when a flag is omitted: ``None`` for the
    overrides we recognise. Tests then layer in whatever overrides they want.
    """
    base: dict[str, object] = {
        "interval": None,
        "iterations": None,
        "run_id": None,
        "analyse_every": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_merge_returns_base_when_no_flags_set() -> None:
    base = MonitorConfig(interval=3, iterations=10, analyse_every=300, run_id="custom")
    result = _merge_monitor_overrides(base, _ns())
    assert result == base


def test_merge_applies_each_override() -> None:
    # ``interval`` accepts a duration string; ``iterations=0`` and
    # ``analyse_every=0`` are the documented "forever" / "disabled" sentinels;
    # ``run_id=""`` clears a TOML-supplied id so the loop auto-generates a slug.
    base = MonitorConfig(interval=3, iterations=1440, analyse_every=300, run_id="from-toml")
    result = _merge_monitor_overrides(
        base,
        _ns(interval="5m", iterations=0, analyse_every="0", run_id=""),
    )
    assert result.interval == 300
    assert result.iterations == 0
    assert result.analyse_every == 0
    assert result.run_id == ""


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"interval": "0s"}, "--interval must be > 0"),
        ({"interval": "banana"}, "invalid duration"),
        ({"iterations": -1}, "--iterations must be >= 0"),
        ({"run_id": "../escape"}, "--run-id"),
        ({"run_id": "with/slash"}, "--run-id"),
    ],
)
def test_merge_rejects_invalid(kwargs: dict[str, object], expected: str) -> None:
    # The TOML loader validates run_id against a path-safety regex; the CLI
    # override path enforces the same rule so hostile values can't slip through.
    with pytest.raises(ValueError, match=expected):
        _merge_monitor_overrides(MonitorConfig(), _ns(**kwargs))


def test_merge_accepts_safe_run_id() -> None:
    result = _merge_monitor_overrides(MonitorConfig(), _ns(run_id="incident_2026-05-04.v1"))
    assert result.run_id == "incident_2026-05-04.v1"


# --- snapshot runner -------------------------------------------------------


def test_snapshot_runner_builds_namespace_for_subrun(tmp_path: Path) -> None:
    captured: list[argparse.Namespace] = []

    def _fake_run(ns: argparse.Namespace) -> int:
        captured.append(ns)
        return 0

    probes_cfg = ProbesConfig()
    runner = _make_snapshot_runner(tmp_path, _fake_run, probes_cfg)
    run_dir = tmp_path / "20260503" / "1430_test"
    run_dir.mkdir(parents=True)
    out_base = run_dir / "snapshots" / "analysis_120000Z"
    runner(run_dir, out_base)

    assert len(captured) == 1
    ns = captured[0]
    assert ns.run == "20260503/1430_test"
    assert ns.root == str(tmp_path)
    assert ns.out == str(out_base)
    assert ns.app == ""
    assert ns.keyword == []
    assert ns.regex == []
    assert ns.stdout is False
    assert ns.probes is probes_cfg


def test_snapshot_runner_passes_none_for_final_analyse(tmp_path: Path) -> None:
    captured: list[argparse.Namespace] = []

    def _record(ns: argparse.Namespace) -> int:
        captured.append(ns)
        return 0

    runner = _make_snapshot_runner(tmp_path, _record, ProbesConfig())
    run_dir = tmp_path / "20260503" / "1430_test"
    run_dir.mkdir(parents=True)
    runner(run_dir, None)
    assert captured[0].out is None


def test_snapshot_runner_raises_on_non_zero_exit(tmp_path: Path) -> None:
    """A soft analyse failure (return code, not exception) must propagate.

    Without this raise the dispatcher's ``snapshot_runner`` try/except would
    silently treat the failure as success — analyse.run uses non-zero exit
    codes for "no app dirs with raw logs" and similar soft errors.
    """
    runner = _make_snapshot_runner(tmp_path, lambda _ns: 2, ProbesConfig())
    run_dir = tmp_path / "20260503" / "1430_test"
    run_dir.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="exited with code 2"):
        runner(run_dir, None)


# --- _print_state_line -----------------------------------------------------


def test_print_state_line_skips_initial_pre_loop_publish(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = MonitorState(
        iteration=0,
        iterations_max=10,
        elapsed_seconds=0,
        captured_total=0,
        next_snapshot_seconds=-1,
    )
    _print_state_line(state)
    assert capsys.readouterr().err == ""


def test_print_state_line_emits_progress(capsys: pytest.CaptureFixture[str]) -> None:
    state = MonitorState(
        iteration=3,
        iterations_max=10,
        elapsed_seconds=42,
        captured_total=999,
        next_snapshot_seconds=-1,
    )
    _print_state_line(state)
    err = capsys.readouterr().err
    assert "iter 3/10" in err
    assert "elapsed=42s" in err
    assert "captured=999" in err
    assert "[done]" not in err


def test_print_state_line_marks_finished(capsys: pytest.CaptureFixture[str]) -> None:
    state = MonitorState(
        iteration=10,
        iterations_max=10,
        elapsed_seconds=600,
        captured_total=12345,
        next_snapshot_seconds=-1,
        finished=True,
    )
    _print_state_line(state)
    err = capsys.readouterr().err
    assert "[done]" in err


def test_print_state_line_omits_max_when_unbounded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = MonitorState(
        iteration=2,
        iterations_max=0,
        elapsed_seconds=10,
        captured_total=5,
        next_snapshot_seconds=-1,
    )
    _print_state_line(state)
    err = capsys.readouterr().err
    assert "iter 2 " in err
    assert "iter 2/" not in err
