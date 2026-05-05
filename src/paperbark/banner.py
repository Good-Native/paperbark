"""Startup banner for ``paperbark monitor``.

Mirrors the bash dispatcher's bracketed banner block (``logs.sh``: rule with
slug, indented Run / Sources / Interval / Iterations / Snapshots / Quit
key-value rows, closing rule). Layout is computed once as a (slug, rows)
pair so the plain-text and Rich-styled paths can't diverge — tests pin the
plain layout and the CLI uses the Rich variant via :func:`print_banner`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from paperbark.dispatcher import MonitorStart
from paperbark.duration import format_elapsed

if TYPE_CHECKING:  # pragma: no cover — types only.
    from rich.console import Console

# Match the bash dispatcher's banner geometry so the Python port lines up
# pixel-for-pixel with the reference. ``RULE_WIDTH`` is the total rule width
# in characters; ``KEY_WIDTH`` is the padded key column inside the indent.
_RULE_WIDTH = 60
_INDENT = "   "
_KEY_WIDTH = 10


def _slug_from_run_dir(run_dir_name: str) -> str:
    """Extract the human-readable slug from ``HHMM_<slug>_<interval>_<duration>``.

    Splits from the right so a user-supplied ``run_id`` containing underscores
    (e.g. ``--run-id my_custom``) survives intact. Falls back to the directory
    name unchanged if the layout doesn't match — any deviation from the
    run-dir contract is then immediately visible in the banner rather than
    silently lost.
    """
    parts = run_dir_name.split("_", 1)
    if len(parts) != 2:
        return run_dir_name
    rest = parts[1]
    # Expected suffix is ``_<interval>_<duration>``; rsplit-2 keeps the slug
    # whole. Anything shorter than three segments means the suffix is absent
    # (older layout, hand-edited path) — return ``rest`` so the banner still
    # shows something useful.
    try:
        slug, _interval, _duration = rest.rsplit("_", 2)
    except ValueError:
        return rest
    return slug


def _build_rows(start: MonitorStart, *, show_quit_hint: bool) -> list[tuple[str, str]]:
    """Return the (key, value) rows for the banner body."""
    monitor = start.monitor
    iterations = monitor.iterations
    if iterations > 0:
        duration_hint = f" (~{format_elapsed(iterations * monitor.interval)})"
    else:
        duration_hint = " (forever)"
    snap_hint = (
        f"every {format_elapsed(monitor.analyse_every)}"
        if monitor.analyse_every > 0
        else "disabled"
    )
    sources_joined = ", ".join(start.source_names) if start.source_names else "—"

    rows: list[tuple[str, str]] = [
        ("Run", str(start.run_dir)),
        ("Sources", sources_joined),
        ("Interval", f"{monitor.interval}s"),
        ("Iterations", f"{iterations}{duration_hint}"),
        ("Snapshots", snap_hint),
    ]
    if show_quit_hint:
        rows.append(("Quit", "press Ctrl+C"))
    return rows


def _top_rule_text(slug: str) -> str:
    pad = max(_RULE_WIDTH - 4 - len(slug), 0)
    return f"── {slug} {'─' * pad}"


def _rule_text() -> str:
    return "─" * _RULE_WIDTH


def _kv_text(key: str, value: str) -> str:
    return f"{_INDENT}{key:<{_KEY_WIDTH}}  {value}"


def render_banner_lines(start: MonitorStart, *, show_quit_hint: bool) -> list[str]:
    """Build the plain-text banner block as a list of lines.

    Used by the non-TTY CLI path and by the test suite to pin layout. The
    Rich-styled path consumes the same row data via :func:`print_banner`.
    """
    slug = _slug_from_run_dir(start.run_dir.name)
    rows = _build_rows(start, show_quit_hint=show_quit_hint)
    lines = [_top_rule_text(slug)]
    lines.extend(_kv_text(k, v) for k, v in rows)
    lines.append(_rule_text())
    return lines


def print_banner(
    start: MonitorStart,
    *,
    console: Console | None = None,
    show_quit_hint: bool = True,
) -> None:
    """Render the banner.

    With a Rich :class:`Console` the slug is bold cyan and rules/values are
    dimmed, matching the bash dispatcher's ``USE_TICKER=true`` styling.
    Without one (non-TTY mode) we write plain ASCII to stderr so the
    captured stdout stream stays clean.
    """
    if console is None:
        import sys

        sys.stderr.write("\n".join(render_banner_lines(start, show_quit_hint=show_quit_hint)))
        sys.stderr.write("\n")
        return

    from rich.text import Text

    slug = _slug_from_run_dir(start.run_dir.name)
    rows = _build_rows(start, show_quit_hint=show_quit_hint)

    pad = max(_RULE_WIDTH - 4 - len(slug), 0)
    top = Text()
    top.append("── ", style="dim")
    top.append(slug, style="bold cyan")
    top.append(" " + ("─" * pad), style="dim")
    console.print(top)

    for key, value in rows:
        row = Text(f"{_INDENT}{key:<{_KEY_WIDTH}}  ")
        row.append(value, style="dim")
        console.print(row)

    console.print(Text(_rule_text(), style="dim"))
