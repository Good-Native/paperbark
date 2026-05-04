"""Tests for paperbark.animator."""

from __future__ import annotations

import io
import time

from rich.console import Console

from paperbark.animator import (
    SPINNER_FRAMES,
    MonitorAnimator,
    render_status,
)
from paperbark.dispatcher import MonitorState


def test_render_status_with_no_state_shows_starting_placeholder() -> None:
    text = render_status(None, frame=0)
    plain = text.plain
    assert "starting" in plain
    # The first frame must be one of the spinner glyphs even with no state.
    assert any(frame in plain for frame in SPINNER_FRAMES)


def test_render_status_includes_iteration_and_capped_total() -> None:
    state = MonitorState(
        iteration=4,
        iterations_max=10,
        elapsed_seconds=125,
        captured_total=2048,
        next_snapshot_seconds=42,
    )
    plain = render_status(state, frame=0).plain
    assert "4 / 10" in plain
    assert "2m 5s" in plain  # elapsed (125s)
    assert "2048" in plain
    assert "next snapshot 42s" in plain


def test_render_status_omits_iter_max_when_unbounded() -> None:
    state = MonitorState(
        iteration=7,
        iterations_max=0,
        elapsed_seconds=1,
        captured_total=0,
        next_snapshot_seconds=-1,
    )
    plain = render_status(state, frame=0).plain
    # Forever runs show just the running iteration count, no "/ N" suffix.
    assert "7" in plain
    assert "/ " not in plain


def test_render_status_omits_snapshot_field_when_disabled() -> None:
    state = MonitorState(
        iteration=1,
        iterations_max=0,
        elapsed_seconds=10,
        captured_total=5,
        next_snapshot_seconds=-1,
    )
    plain = render_status(state, frame=0).plain
    assert "next snapshot" not in plain


def test_render_status_overrides_let_animator_tick_between_publishes() -> None:
    # The redraw thread doesn't get a fresh MonitorState every frame, so it
    # passes overrides to keep the elapsed clock alive between publishes.
    state = MonitorState(
        iteration=2,
        iterations_max=5,
        elapsed_seconds=10,
        captured_total=100,
        next_snapshot_seconds=20,
    )
    plain = render_status(state, frame=0, elapsed_override=15, next_snapshot_override=15).plain
    assert "15s" in plain
    assert "next snapshot 15s" in plain


def test_render_status_cycles_through_spinner_frames() -> None:
    state = MonitorState(
        iteration=1,
        iterations_max=0,
        elapsed_seconds=0,
        captured_total=0,
        next_snapshot_seconds=-1,
    )
    seen = {render_status(state, frame=i).plain[3] for i in range(len(SPINNER_FRAMES))}
    # Every frame in the wheel should have been picked over one full cycle.
    assert seen == set(SPINNER_FRAMES)


def test_monitor_animator_smoke_renders_to_console() -> None:
    # The Live thread runs at default fps; we sleep just long enough for one
    # tick + the immediate update to land. force_terminal makes Live actually
    # render even though our file is a StringIO rather than a real TTY.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    state = MonitorState(
        iteration=3,
        iterations_max=10,
        elapsed_seconds=12,
        captured_total=99,
        next_snapshot_seconds=8,
    )
    with MonitorAnimator(console, fps=20) as ticker:
        ticker.update(state)
        time.sleep(0.15)  # let the redraw thread tick at least twice
    output = buf.getvalue()
    assert "3 / 10" in output
    assert "99" in output


def test_monitor_animator_can_be_used_without_publishing_state() -> None:
    # Entering and exiting without ever calling .update() must not raise; the
    # animator falls back to the "starting…" placeholder.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system=None)
    with MonitorAnimator(console, fps=20):
        time.sleep(0.1)
    output = buf.getvalue()
    assert "starting" in output
