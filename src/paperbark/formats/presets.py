"""Built-in format presets.

Each preset is a :class:`RegexFormat` instance with a tested pattern and
a level map where applicable. Patterns aim to be permissive enough to
handle the common shapes operators see in the wild without being so
loose that they false-match on Fly's mixed structured/plain output.
"""

from __future__ import annotations

import re

from paperbark.formats.regex import RegexFormat
from paperbark.probes._record import CanonicalRecord

# Apache combined log format. Reference example:
#   127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0"
#   200 2326 "http://www.example.com/start.html" "Mozilla/4.08 [en] (Win98; I ;Nav)"
_APACHE_COMBINED_RE = re.compile(
    r"(?P<host>\S+)\s+\S+\s+\S+\s+\["
    r"(?P<timestamp>[^\]]+)\]\s+"
    r'"\S+\s+(?P<message>\S+)\s+\S+"\s+'
    r"(?P<status>\d{3})\s+\S+"
)
_APACHE_TS_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

# RFC 5424 syslog. Reference example:
#   <165>1 2003-10-11T22:14:15.003Z mymachine.example.com evntslog - ID47
#   [exampleSDID@32473 iut="3" eventSource="Application"] BOMAn application event log entry
_SYSLOG_RFC5424_RE = re.compile(
    r"^<(?P<priority>\d+)>\d+\s+"
    r"(?P<timestamp>\S+)\s+"
    r"\S+\s+"
    r"(?P<component>\S+)\s+"
    r"\S+\s+"
    r"\S+\s+"
    r"(?:\[[^\]]*\]\s+)?"
    r"(?P<message>.*)$"
)

# Severity (priority & 7) → canonical level. Source: RFC 5424 §6.2.1.
_SYSLOG_SEVERITY_LEVELS = {
    "0": "fatal",
    "1": "fatal",
    "2": "error",
    "3": "error",
    "4": "warn",
    "5": "info",
    "6": "info",
    "7": "debug",
}


def apache_combined() -> RegexFormat:
    """Apache "combined" log format (also nginx default)."""
    return RegexFormat(
        name="apache-combined",
        pattern=_APACHE_COMBINED_RE,
        ts_format=_APACHE_TS_FORMAT,
    )


def nginx_default() -> RegexFormat:
    """nginx default access log format (identical to Apache combined)."""
    return RegexFormat(
        name="nginx-default",
        pattern=_APACHE_COMBINED_RE,
        ts_format=_APACHE_TS_FORMAT,
    )


def syslog_rfc5424() -> RegexFormat:
    """RFC 5424 syslog with priority-derived level."""
    return _SyslogRFC5424Format()


class _SyslogRFC5424Format(RegexFormat):
    """RegexFormat subclass that derives level from the priority byte."""

    def __init__(self) -> None:
        super().__init__(name="syslog-rfc5424", pattern=_SYSLOG_RFC5424_RE)

    def parse(self, raw_line: str) -> CanonicalRecord:
        record = super().parse(raw_line)
        match = self.pattern.search(raw_line)
        if match is None:
            return record
        priority = match.group("priority")
        if not priority:
            return record
        try:
            severity = str(int(priority) & 7)
        except ValueError:
            return record
        # CanonicalRecord is frozen; rebuild with the derived level.
        return CanonicalRecord(
            timestamp=record.timestamp,
            level=_SYSLOG_SEVERITY_LEVELS.get(severity, ""),
            message=record.message,
            component=record.component,
            status=record.status,
            duration_ms=record.duration_ms,
            raw_line=record.raw_line,
        )
