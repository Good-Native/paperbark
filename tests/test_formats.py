"""Tests for the format layer."""

from __future__ import annotations

import re

from paperbark.formats import (
    Format,
    JsonKeysFormat,
    RegexFormat,
    apache_combined,
    nginx_default,
    registered_formats,
    syslog_rfc5424,
)
from paperbark.probes._record import CanonicalRecord


def test_registered_formats_lists_every_preset() -> None:
    names = set(registered_formats())
    assert names == {"json", "apache-combined", "nginx-default", "syslog-rfc5424"}


def test_json_format_satisfies_protocol() -> None:
    fmt: Format = JsonKeysFormat()
    assert isinstance(fmt, Format)


def test_json_format_extracts_default_keys() -> None:
    record = JsonKeysFormat().parse(
        '2026-05-03T02:00:01Z {"level":"WARN","msg":"oops","status":503,"duration_ms":12.5,'
        '"service":"api"}\n'
    )
    assert record.timestamp == "2026-05-03T02:00:01+00:00"
    assert record.level == "warn"
    assert record.message == "oops"
    assert record.status == "503"
    assert record.duration_ms == 12.5
    assert record.component == "api"


def test_json_format_honours_custom_message_keys() -> None:
    record = JsonKeysFormat(message_keys=("text",)).parse('2026-05-03T02:00:01Z {"text":"hello"}\n')
    assert record.message == "hello"


def test_json_format_falls_back_to_leading_timestamp() -> None:
    record = JsonKeysFormat().parse('2026-05-03T02:00:01Z {"msg":"no timestamp key"}\n')
    assert record.timestamp == "2026-05-03T02:00:01+00:00"


def test_json_format_returns_empty_canonical_for_plain_lines() -> None:
    record = JsonKeysFormat().parse("plain text no json\n")
    assert record.timestamp == ""
    assert record.level == ""
    assert record.message == ""
    assert record.duration_ms is None


def test_regex_format_with_named_groups() -> None:
    fmt = RegexFormat(
        "demo",
        re.compile(
            r"^(?P<timestamp>\S+)\s+(?P<level>\w+)\s+(?P<component>\w+):" r"\s+(?P<message>.*)$"
        ),
    )
    record = fmt.parse("2026-05-03T02:00:01Z error api: it broke\n")
    assert record.timestamp == "2026-05-03T02:00:01+00:00"
    assert record.level == "error"
    assert record.component == "api"
    assert "it broke" in record.message


def test_regex_format_returns_empty_when_pattern_does_not_match() -> None:
    fmt = RegexFormat("demo", re.compile(r"^never_matches"))
    record = fmt.parse("anything\n")
    assert record == CanonicalRecord(
        timestamp="",
        level="",
        message="",
        component="",
        status="",
        duration_ms=None,
        raw_line="anything\n",
    )


def test_regex_format_uses_strptime_for_non_iso_timestamps() -> None:
    fmt = RegexFormat(
        "apache-like",
        re.compile(r"\[(?P<timestamp>[^\]]+)\]"),
        ts_format="%d/%b/%Y:%H:%M:%S %z",
    )
    record = fmt.parse("[10/Oct/2000:13:55:36 -0700]\n")
    assert record.timestamp == "2000-10-10T13:55:36-07:00"


def test_apache_combined_preset_parses_canonical_example() -> None:
    line = (
        "127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "
        '"GET /apache_pb.gif HTTP/1.0" 200 2326 '
        '"http://www.example.com/start.html" "Mozilla/4.08"\n'
    )
    record = apache_combined().parse(line)
    assert record.timestamp == "2000-10-10T13:55:36-07:00"
    assert record.status == "200"
    assert record.message == "/apache_pb.gif"


def test_nginx_default_uses_same_shape_as_apache() -> None:
    line = '10.0.0.1 - - [01/Jan/2026:00:00:00 +0000] "POST /api HTTP/1.1" 503 0 "-" "-"\n'
    record = nginx_default().parse(line)
    assert record.status == "503"
    assert record.timestamp == "2026-01-01T00:00:00+00:00"


def test_syslog_rfc5424_derives_level_from_priority() -> None:
    line = (
        "<11>1 2003-10-11T22:14:15Z mymachine.example.com evntslog - ID47 "
        '[exampleSDID@32473 iut="3"] BOMAn application event log entry\n'
    )
    record = syslog_rfc5424().parse(line)
    # Priority 11 → severity 3 → "error" per the RFC 5424 mapping.
    assert record.level == "error"
    assert record.timestamp == "2003-10-11T22:14:15+00:00"
    assert record.component == "evntslog"
    assert "application event log entry" in record.message


def test_syslog_rfc5424_handles_info_severity() -> None:
    # Priority 14 → facility 1, severity 6 → "info".
    line = "<14>1 2026-05-03T02:00:01Z host comp - - - hello world\n"
    record = syslog_rfc5424().parse(line)
    assert record.level == "info"
