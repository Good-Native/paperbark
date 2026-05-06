"""Tests for the cursor-based dedup filter."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from paperbark.cursor import (
    apply_to_file,
    cli,
    filter_lines,
    filter_stream,
)


def test_empty_cursor_keeps_all_timestamped_lines() -> None:
    lines = [
        "2026-05-03T02:00:00Z first\n",
        "2026-05-03T02:00:01Z second\n",
    ]
    kept, cursor = filter_lines(lines, "")
    assert kept == lines
    assert cursor == "2026-05-03T02:00:01+00:00"


def test_cursor_drops_earlier_and_equal_keeps_later() -> None:
    cursor = "2026-05-03T02:00:00+00:00"
    lines = [
        "2026-05-03T02:00:00Z at-cursor\n",
        "2026-05-03T01:59:59Z before\n",
        "2026-05-03T02:00:01Z after\n",
    ]
    kept, new_cursor = filter_lines(lines, cursor)
    assert kept == ["2026-05-03T02:00:01Z after\n"]
    assert new_cursor == "2026-05-03T02:00:01+00:00"


def test_continuation_lines_track_their_header() -> None:
    cursor = "2026-05-03T02:00:00+00:00"
    lines = [
        "2026-05-03T01:59:59Z stale header\n",
        "  stale stack frame\n",
        "  another stale frame\n",
        "2026-05-03T02:00:01Z fresh header\n",
        "  fresh stack frame\n",
        "  another fresh frame\n",
    ]
    kept, _ = filter_lines(lines, cursor)
    assert kept == [
        "2026-05-03T02:00:01Z fresh header\n",
        "  fresh stack frame\n",
        "  another fresh frame\n",
    ]


def test_ansi_prefix_stripped_before_match() -> None:
    lines = ["\x1b[2m2026-05-03T02:00:01Z\x1b[0m colourful\n"]
    kept, cursor = filter_lines(lines, "")
    assert kept == lines
    assert cursor == "2026-05-03T02:00:01+00:00"


def test_orphan_continuation_lines_before_first_header_are_dropped() -> None:
    lines = [
        "no timestamp leading\n",
        "still no timestamp\n",
        "2026-05-03T02:00:01Z header\n",
        "  legitimate continuation\n",
    ]
    kept, _ = filter_lines(lines, "")
    assert kept == [
        "2026-05-03T02:00:01Z header\n",
        "  legitimate continuation\n",
    ]


def test_filter_stream_writes_via_callback() -> None:
    out = StringIO()
    cursor = filter_stream(
        ["2026-05-03T02:00:01Z one\n"],
        "",
        write=out.write,
    )
    assert out.getvalue() == "2026-05-03T02:00:01Z one\n"
    assert cursor == "2026-05-03T02:00:01+00:00"


def test_apply_to_file_creates_cursor_and_parents(tmp_path: Path) -> None:
    cursor_path = tmp_path / "nested" / "dir" / ".cursor"
    lines = ["2026-05-03T02:00:01Z hello\n"]
    kept = apply_to_file(lines, cursor_path)
    assert kept == lines
    assert cursor_path.read_text(encoding="utf-8") == "2026-05-03T02:00:01+00:00"


def test_apply_to_file_filters_against_existing_cursor(tmp_path: Path) -> None:
    cursor_path = tmp_path / ".cursor"
    cursor_path.write_text("2026-05-03T02:00:01+00:00", encoding="utf-8")
    lines = [
        "2026-05-03T02:00:00Z stale\n",
        "2026-05-03T02:00:02Z fresh\n",
    ]
    kept = apply_to_file(lines, cursor_path)
    assert kept == ["2026-05-03T02:00:02Z fresh\n"]
    assert cursor_path.read_text(encoding="utf-8") == "2026-05-03T02:00:02+00:00"


def test_apply_to_file_does_not_rewrite_unchanged_cursor(tmp_path: Path) -> None:
    cursor_path = tmp_path / ".cursor"
    cursor_path.write_text("2026-05-03T02:00:01+00:00", encoding="utf-8")
    mtime_before = cursor_path.stat().st_mtime_ns
    apply_to_file(
        ["2026-05-03T02:00:00Z stale\n"],
        cursor_path,
    )
    assert cursor_path.stat().st_mtime_ns == mtime_before


def test_format_path_advances_cursor_from_apache_timestamp() -> None:
    """Apache-combined lines have no leading ISO timestamp, so the default
    path would drop them all. With the format attached the cursor advances
    from the bracketed Apache timestamp instead.
    """
    from paperbark.formats import apache_combined

    lines = [
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /a HTTP/1.0" 200 12\n',
        '127.0.0.1 - - [10/Oct/2000:13:55:38 -0700] "GET /b HTTP/1.0" 200 34\n',
    ]
    kept, cursor = filter_lines(lines, "", line_format=apache_combined())
    assert kept == lines
    assert cursor == "2000-10-10T13:55:38-07:00"


def test_format_path_drops_lines_at_or_before_cursor() -> None:
    from paperbark.formats import apache_combined

    cursor = "2000-10-10T13:55:36-07:00"
    lines = [
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET /eq HTTP/1.0" 200 1\n',
        '127.0.0.1 - - [10/Oct/2000:13:55:35 -0700] "GET /old HTTP/1.0" 200 2\n',
        '127.0.0.1 - - [10/Oct/2000:13:55:40 -0700] "GET /new HTTP/1.0" 200 3\n',
    ]
    kept, new_cursor = filter_lines(lines, cursor, line_format=apache_combined())
    assert kept == ['127.0.0.1 - - [10/Oct/2000:13:55:40 -0700] "GET /new HTTP/1.0" 200 3\n']
    assert new_cursor == "2000-10-10T13:55:40-07:00"


def test_format_path_drops_unparseable_lines() -> None:
    """Banner / preamble lines that don't match the format have no timestamp
    signal, so they are dropped — there is no leading-ISO "header" to carry
    them through.
    """
    from paperbark.formats import apache_combined

    lines = [
        "[Mon Oct 10 13:55:00 2000] [notice] Apache configured -- resuming normal operations\n",
        '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET / HTTP/1.0" 200 1\n',
    ]
    kept, _ = filter_lines(lines, "", line_format=apache_combined())
    assert kept == ['127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] "GET / HTTP/1.0" 200 1\n']


def test_format_path_works_with_syslog_rfc5424() -> None:
    from paperbark.formats import syslog_rfc5424

    lines = [
        "<165>1 2003-10-11T22:14:15.003Z host evnt - ID47 - first\n",
        "<165>1 2003-10-11T22:14:16.000Z host evnt - ID47 - second\n",
    ]
    kept, cursor = filter_lines(lines, "", line_format=syslog_rfc5424())
    assert kept == lines
    # Format normalises the trailing Z to +00:00 and trims to ISO seconds.
    assert cursor == "2003-10-11T22:14:16+00:00"


def test_cli_wrong_arg_count_returns_two() -> None:
    assert cli([]) == 2
    assert cli(["one", "two"]) == 2


def test_cli_happy_path_persists_cursor_and_emits_kept_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor_path = tmp_path / ".cursor"
    monkeypatch.setattr(
        "sys.stdin",
        StringIO("2026-05-03T02:00:00Z first\n2026-05-03T02:00:01Z second\n"),
    )
    out = StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = cli([str(cursor_path)])
    assert rc == 0
    assert out.getvalue() == ("2026-05-03T02:00:00Z first\n2026-05-03T02:00:01Z second\n")
    assert cursor_path.read_text(encoding="utf-8") == "2026-05-03T02:00:01+00:00"
