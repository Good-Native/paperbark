"""Canonical record + parser.

Every probe takes a :class:`CanonicalRecord`. The mapping from a raw line
to the canonical form lives here, so probes never branch on source-specific
JSON keys or text shapes. The format layer (when it lands) will replace
:func:`parse_line` for non-Fly sources without changing any probe.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LEADING_TS_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
HTTP_ACCESS_RE = re.compile(r'HTTP/\d\.\d"\s+(\d{3})\s+\d+')
STATUS_FIELD_RE = re.compile(r'\bstatus(?:_code)?["\']?\s*[=:]\s*"?(\d{3})\b')

ISO_KEYS = ("time", "timestamp", "@timestamp", "ts", "created_at")
LEVEL_KEYS = ("level",)
MESSAGE_KEYS = ("msg", "message")
COMPONENT_KEYS = ("component", "service", "app", "logger")
STATUS_KEYS = ("status", "status_code", "statusCode", "http_status")
DURATION_MS_KEYS = ("dur_ms", "duration_ms", "latency_ms", "elapsed_ms", "took_ms")


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    """The shape every probe consumes.

    Empty strings (rather than ``None``) are used for unknown text fields
    so probes can use them in regex/string contexts without conditional
    handling. ``duration_ms`` stays ``None`` when no duration field was
    found — the difference between "no duration" and "duration of zero"
    matters for the latency probe's percentile maths.
    """

    timestamp: str
    level: str
    message: str
    component: str
    status: str
    duration_ms: float | None
    raw_line: str


def strip_ansi(line: str) -> str:
    """Remove SGR escape sequences from ``line``."""
    return ANSI_RE.sub("", line)


def iso_minute(timestamp: str) -> str:
    """Truncate an ISO timestamp to its YYYY-MM-DDTHH:MM minute."""
    return timestamp[:16] if len(timestamp) >= 16 else timestamp


def parse_line(raw_line: str) -> CanonicalRecord:
    """Convert a raw log line into a :class:`CanonicalRecord`.

    Accepts lines with or without an embedded JSON object. JSON keys are
    probed in priority order (see ``ISO_KEYS`` etc.). Failing JSON, the
    function falls back to regex extraction on the cleaned text — the
    leading ISO timestamp and HTTP status from access-log shapes.
    """
    cleaned = strip_ansi(raw_line)
    rec = _parse_record_dict(cleaned)
    return CanonicalRecord(
        timestamp=_extract_timestamp(rec, cleaned),
        level=_extract_string(rec, LEVEL_KEYS).lower(),
        message=_extract_string(rec, MESSAGE_KEYS),
        component=_extract_string(rec, COMPONENT_KEYS),
        status=_extract_status(rec, cleaned),
        duration_ms=_extract_duration_ms(rec),
        raw_line=raw_line,
    )


def _parse_record_dict(line: str) -> dict[str, Any] | None:
    idx = line.find("{")
    if idx == -1:
        return None
    try:
        rec = json.loads(line[idx:])
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _extract_timestamp(rec: dict[str, Any] | None, line: str) -> str:
    if rec:
        for key in ISO_KEYS:
            value = rec.get(key)
            if not value:
                continue
            normalised = str(value).replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(normalised).isoformat(timespec="seconds")
            except ValueError:
                continue
    match = LEADING_TS_RE.match(line)
    if match is None:
        return ""
    raw = match.group(1).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).isoformat(timespec="seconds")
    except ValueError:
        return raw[:19]


def _extract_string(rec: dict[str, Any] | None, keys: tuple[str, ...]) -> str:
    if not rec:
        return ""
    for key in keys:
        value = rec.get(key)
        if value:
            return str(value)
    return ""


def _extract_status(rec: dict[str, Any] | None, line: str) -> str:
    if rec:
        for key in STATUS_KEYS:
            value = rec.get(key)
            if isinstance(value, int | str):
                text = str(value)
                if text.isdigit() and len(text) == 3:
                    return text
    match = HTTP_ACCESS_RE.search(line) or STATUS_FIELD_RE.search(line)
    if match is not None:
        return match.group(1)
    return ""


def _extract_duration_ms(rec: dict[str, Any] | None) -> float | None:
    if not rec:
        return None
    for key in DURATION_MS_KEYS:
        value = rec.get(key)
        if isinstance(value, int | float):
            return float(value)
    # `duration` follows Go/zerolog convention: integer nanoseconds.
    # Inferring units from magnitude would silently misclassify
    # millisecond-scale values, so we only honour the explicit *_ms keys
    # above for milliseconds and treat bare `duration` as nanoseconds.
    bare = rec.get("duration")
    if isinstance(bare, int | float):
        return float(bare) / 1_000_000
    return None
