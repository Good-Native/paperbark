"""Tests for canonical record parsing."""

from __future__ import annotations

from paperbark.probes._record import (
    CanonicalRecord,
    iso_minute,
    parse_line,
    strip_ansi,
)


def test_strip_ansi_removes_sgr_sequences() -> None:
    coloured = "\x1b[2m2026-05-03T02:00:01Z\x1b[0m hello"
    assert strip_ansi(coloured) == "2026-05-03T02:00:01Z hello"


def test_iso_minute_truncates_to_hh_mm() -> None:
    assert iso_minute("2026-05-03T02:00:01") == "2026-05-03T02:00"
    assert iso_minute("short") == "short"


def test_parse_line_with_no_json_keeps_leading_timestamp() -> None:
    record = parse_line("2026-05-03T02:00:01Z hello world\n")
    assert record.timestamp == "2026-05-03T02:00:01+00:00"
    assert record.level == ""
    assert record.message == ""
    assert record.status == ""
    assert record.duration_ms is None
    assert record.raw_line == "2026-05-03T02:00:01Z hello world\n"


def test_parse_line_extracts_json_fields() -> None:
    record = parse_line(
        '2026-05-03T02:00:01Z {"level":"WARN","msg":"oops","service":"api","status":503}\n'
    )
    assert record.level == "warn"
    assert record.message == "oops"
    assert record.component == "api"
    assert record.status == "503"


def test_parse_line_uses_json_timestamp_over_leading() -> None:
    record = parse_line('2026-05-03T02:00:01Z {"time":"2026-05-03T02:00:05Z","msg":"x"}\n')
    assert record.timestamp == "2026-05-03T02:00:05+00:00"


def test_parse_line_falls_back_when_json_timestamp_invalid() -> None:
    record = parse_line('2026-05-03T02:00:01Z {"time":"not-a-date","msg":"x"}\n')
    assert record.timestamp == "2026-05-03T02:00:01+00:00"


def test_parse_line_extracts_status_from_access_log() -> None:
    record = parse_line('2026-05-03T02:00:01Z 1.2.3.4 - - "GET /x HTTP/1.1" 503 1234\n')
    assert record.status == "503"


def test_parse_line_ignores_non_three_digit_status() -> None:
    record = parse_line('2026-05-03T02:00:01Z {"status":42}\n')
    assert record.status == ""


def test_parse_line_duration_ms_from_explicit_key() -> None:
    record = parse_line('2026-05-03T02:00:01Z {"latency_ms":250.5,"msg":"x"}\n')
    assert record.duration_ms == 250.5


def test_parse_line_duration_ms_from_bare_duration_in_nanoseconds() -> None:
    # Go/zerolog convention: bare `duration` is nanoseconds.
    record = parse_line('2026-05-03T02:00:01Z {"duration":250000000,"msg":"x"}\n')
    assert record.duration_ms == 250.0


def test_parse_line_duration_ms_absent_returns_none() -> None:
    record = parse_line('2026-05-03T02:00:01Z {"msg":"x"}\n')
    assert record.duration_ms is None


def test_parse_line_strips_ansi_before_parsing() -> None:
    record = parse_line('\x1b[2m2026-05-03T02:00:01Z\x1b[0m {"level":"info","msg":"x"}\n')
    assert record.timestamp == "2026-05-03T02:00:01+00:00"
    assert record.level == "info"


def test_canonical_record_is_frozen() -> None:
    record = CanonicalRecord(
        timestamp="t",
        level="info",
        message="m",
        component="c",
        status="200",
        duration_ms=1.0,
        raw_line="r",
    )
    try:
        record.level = "warn"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("CanonicalRecord should be frozen")
