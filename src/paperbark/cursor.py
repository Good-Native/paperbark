"""Cursor-based dedup filter for log captures.

Port of `reference/filter_since.py`. Stops Fly's overlapping log windows
from re-emitting lines we have already persisted earlier in the run.

The filter keeps lines whose leading ISO timestamp is strictly greater
than the supplied cursor and updates the cursor to the maximum timestamp
seen. Lines without a parseable leading timestamp (multi-line panic
stack traces, for example) are kept iff the most recent timestamped
header was kept; otherwise they are stale carry-over from a prior
overlapping capture.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LEADING_TS_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


def _normalise(ts: str) -> str:
    """Return a lexicographically comparable form of an ISO timestamp.

    Fly emits trailing `Z`; some sources emit `+00:00`. Comparison is
    plain string ordering, so we unify on `+00:00` to match the bash
    original. We deliberately do not convert offsets to UTC — that would
    diverge from the reference behaviour and is not required by any
    current source.
    """
    return ts.replace("Z", "+00:00")


def filter_stream(
    lines: Iterable[str],
    cursor: str,
    *,
    write: Callable[[str], object],
) -> str:
    """Stream-filter ``lines`` against ``cursor``, returning the new cursor.

    Each kept line is passed to ``write``. The new cursor is the maximum
    timestamp seen across the input (or the original cursor if nothing
    advanced it).
    """
    new_max = cursor
    header_emitted = False
    for line in lines:
        clean = ANSI_RE.sub("", line)
        match = LEADING_TS_RE.match(clean)
        if match is None:
            if header_emitted:
                write(line)
            continue
        ts = _normalise(match.group(1))
        if cursor and ts <= cursor:
            header_emitted = False
            continue
        write(line)
        header_emitted = True
        if ts > new_max:
            new_max = ts
    return new_max


def filter_lines(lines: Iterable[str], cursor: str) -> tuple[list[str], str]:
    """Eager wrapper over :func:`filter_stream`.

    Returns the kept lines as a list alongside the new cursor. Convenient
    for tests and small captures; for large streams prefer
    :func:`filter_stream` with a file ``.write`` sink.
    """
    kept: list[str] = []
    new_cursor = filter_stream(lines, cursor, write=kept.append)
    return kept, new_cursor


def apply_to_file(lines: Iterable[str], cursor_path: Path) -> list[str]:
    """Filter ``lines`` using the cursor stored at ``cursor_path``.

    Reads the existing cursor (empty string if absent), filters, and
    writes the new cursor only when it has advanced. Creates parent
    directories on first write.
    """
    cursor = ""
    if cursor_path.exists():
        cursor = cursor_path.read_text(encoding="utf-8").strip()
    kept, new_cursor = filter_lines(lines, cursor)
    if new_cursor and new_cursor != cursor:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text(new_cursor, encoding="utf-8")
    return kept


def cli(argv: list[str] | None = None) -> int:
    """Stand-alone CLI matching ``reference/filter_since.py``.

    Reads stdin, writes kept lines to stdout, persists the new cursor.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 1:
        sys.stderr.write("usage: python -m paperbark.cursor <cursor_file>\n")
        return 2
    cursor_path = Path(args[0])
    new_cursor = filter_stream(sys.stdin, _read_cursor(cursor_path), write=sys.stdout.write)
    _write_cursor_if_advanced(cursor_path, new_cursor)
    return 0


def _read_cursor(cursor_path: Path) -> str:
    if not cursor_path.exists():
        return ""
    return cursor_path.read_text(encoding="utf-8").strip()


def _write_cursor_if_advanced(cursor_path: Path, new_cursor: str) -> None:
    previous = _read_cursor(cursor_path)
    if new_cursor and new_cursor != previous:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text(new_cursor, encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(cli())
    except KeyboardInterrupt:
        # The dispatcher forwards SIGINT to every child in the pipe; exit
        # quietly so the parent's report writes without a traceback bleeding
        # into the terminal.
        raise SystemExit(130) from None
