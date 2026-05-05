"""Tests for paperbark.animator."""

from __future__ import annotations

import io
import time

import pytest
from rich.console import Console

from paperbark.animator import (
    SPINNER_FRAMES,
    MonitorAnimator,
    render_status,
)
from paperbark.dispatcher import MonitorState


def _state(
    *,
    iteration: int = 1,
    iterations_max: int = 0,
    elapsed_seconds: int = 0,
    captured_total: int = 0,
    next_snapshot_seconds: int = -1,
) -> MonitorState:
    return MonitorState(
        iteration=iteration,
        iterations_max=iterations_max,
        elapsed_seconds=elapsed_seconds,
        captured_total=captured_total,
        next_snapshot_seconds=next_snapshot_seconds,
    )


@pytest.mark.parametrize(
    "state, frame, overrides, must_contain, must_not_contain",
    [
        # No state → "starting" placeholder with a valid spinner glyph.
        (None, 0, {}, ["starting"], []),
        # Bounded run: iter/total, elapsed, captured, snapshot countdown all rendered.
        (
            _state(
                iteration=4,
                iterations_max=10,
                elapsed_seconds=125,
                captured_total=2048,
                next_snapshot_seconds=42,
            ),
            0,
            {},
            ["4 / 10", "2m 5s", "2048", "next snapshot 42s"],
            [],
        ),
        # Unbounded run + snapshots disabled: no "/ N" suffix, no snapshot field.
        (
            _state(iteration=7, elapsed_seconds=1),
            0,
            {},
            ["7"],
            ["/ ", "next snapshot"],
        ),
        # Overrides keep the elapsed/snapshot clocks ticking between publishes.
        (
            _state(
                iteration=2,
                iterations_max=5,
                elapsed_seconds=10,
                captured_total=100,
                next_snapshot_seconds=20,
            ),
            0,
            {"elapsed_override": 15, "next_snapshot_override": 15},
            ["15s", "next snapshot 15s"],
            [],
        ),
    ],
)
def test_render_status(
    state: MonitorState | None,
    frame: int,
    overrides: dict[str, int],
    must_contain: list[str],
    must_not_contain: list[str],
) -> None:
    plain = render_status(state, frame=frame, **overrides).plain
    for needle in must_contain:
        assert needle in plain
    for forbidden in must_not_contain:
        assert forbidden not in plain
    # Spinner glyph must always be one of the wheel frames.
    assert any(g in plain for g in SPINNER_FRAMES)


def test_render_status_cycles_through_spinner_frames() -> None:
    state = _state(iteration=1)
    seen = {render_status(state, frame=i).plain[3] for i in range(len(SPINNER_FRAMES))}
    assert seen == set(SPINNER_FRAMES)


def _wait_for(buf: io.StringIO, needle: str, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in buf.getvalue():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {needle!r}; got {buf.getvalue()!r}")


def test_monitor_animator_smoke_renders_to_console() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    state = _state(
        iteration=3,
        iterations_max=10,
        elapsed_seconds=12,
        captured_total=99,
        next_snapshot_seconds=8,
    )
    with MonitorAnimator(console, fps=20) as ticker:
        ticker.update(state)
        _wait_for(buf, "3 / 10")
    output = buf.getvalue()
    assert "3 / 10" in output
    assert "99" in output
