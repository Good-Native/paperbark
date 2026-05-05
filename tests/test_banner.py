"""Tests for paperbark.banner."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from paperbark.banner import print_banner, render_banner_lines
from paperbark.config import MonitorConfig
from paperbark.dispatcher import MonitorStart


def _start(
    *,
    run_dir: Path = Path("logs/20260503/1430_nimble-azure_3s_1h"),
    source_names: tuple[str, ...] = ("api", "worker"),
    interval: int = 3,
    iterations: int = 1200,
    analyse_every: int = 300,
) -> MonitorStart:
    return MonitorStart(
        run_dir=run_dir,
        source_names=source_names,
        monitor=MonitorConfig(
            interval=interval,
            iterations=iterations,
            analyse_every=analyse_every,
            cleanup_enabled=False,
        ),
    )


def test_render_banner_lines_matches_bash_layout() -> None:
    """Banner rows mirror the bash dispatcher's bracketed block.

    Slug extracted from the run-dir name, indented key/value rows, and
    matching closing rule width. ``Quit`` row appears only when requested.
    """
    lines = render_banner_lines(_start(), show_quit_hint=True)
    assert lines[0].startswith("── nimble-azure ")
    # Top rule pads to 60 chars total.
    assert len(lines[0]) == 60
    body = "\n".join(lines)
    assert "   Run         logs/20260503/1430_nimble-azure_3s_1h" in body
    assert "   Sources     api, worker" in body
    assert "   Interval    3s" in body
    assert "   Iterations  1200 (~1h 0m)" in body
    assert "   Snapshots   every 5m 0s" in body
    assert "   Quit        press Ctrl+C" in body
    assert lines[-1] == "─" * 60


def test_render_banner_lines_omits_quit_hint() -> None:
    lines = render_banner_lines(_start(), show_quit_hint=False)
    assert all("Quit" not in line for line in lines)


def test_render_banner_lines_handles_forever_and_disabled() -> None:
    """Iterations=0 ⇒ ``(forever)`` and analyse_every=0 ⇒ ``disabled``."""
    lines = render_banner_lines(
        _start(iterations=0, analyse_every=0),
        show_quit_hint=False,
    )
    body = "\n".join(lines)
    assert "Iterations  0 (forever)" in body
    assert "Snapshots   disabled" in body


def test_render_banner_lines_falls_back_when_run_dir_lacks_slug() -> None:
    """An unexpected run-dir layout shows the directory name verbatim.

    Better to surface the deviation in the banner than silently mangle it.
    """
    lines = render_banner_lines(
        _start(run_dir=Path("logs/odd")),
        show_quit_hint=False,
    )
    assert lines[0].startswith("── odd ")


def test_render_banner_lines_preserves_underscore_in_user_run_id() -> None:
    """A user-supplied ``run_id`` with underscores must survive intact.

    The run-dir is ``HHMM_<slug>_<interval>_<duration>`` so a left-find on
    ``_`` would clip ``my_custom`` to ``my``. Splitting from the right keeps
    the slug whole.
    """
    lines = render_banner_lines(
        _start(run_dir=Path("logs/20260503/1430_my_custom_3s_1h")),
        show_quit_hint=False,
    )
    assert lines[0].startswith("── my_custom ")


def test_print_banner_writes_plain_to_stderr_without_console(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_banner(_start(), console=None, show_quit_hint=False)
    err = capsys.readouterr().err
    assert "── nimble-azure " in err
    assert "Run        " in err
    # Without a Rich console the output carries no ANSI escapes.
    assert "\x1b[" not in err


def test_print_banner_with_rich_console_styles_slug() -> None:
    """With a Rich Console the slug is bold cyan; rules and values are dimmed.

    Force ``force_terminal=True`` so ANSI escapes are emitted regardless of
    whether the test runner has a TTY attached.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80)
    print_banner(_start(), console=console, show_quit_hint=True)
    output = buf.getvalue()
    assert "nimble-azure" in output
    assert "\x1b[" in output  # styling actually applied
    assert "Run" in output
    assert "press Ctrl+C" in output
