"""JSON-keys format: extract canonical fields from an embedded JSON object.

This is the default format for sources that emit structured logs (Fly,
Cloudflare Workers, Kubernetes structured stdout, …). The set of keys
to consult for each canonical field is configurable so non-Fly producers
can match without forking — pass ``message_keys=("text", "msg", "message")``
or similar at construction.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from paperbark.probes._record import CanonicalRecord

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LEADING_TS_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
HTTP_ACCESS_RE = re.compile(r'HTTP/\d\.\d"\s+(\d{3})\s+\d+')
STATUS_FIELD_RE = re.compile(r'\bstatus(?:_code)?["\']?\s*[=:]\s*"?(\d{3})\b')

DEFAULT_TIMESTAMP_KEYS = ("time", "timestamp", "@timestamp", "ts", "created_at")
DEFAULT_LEVEL_KEYS = ("level",)
DEFAULT_MESSAGE_KEYS = ("msg", "message")
DEFAULT_COMPONENT_KEYS = ("component", "service", "app", "logger")
DEFAULT_STATUS_KEYS = ("status", "status_code", "statusCode", "http_status")
DEFAULT_DURATION_MS_KEYS = (
    "dur_ms",
    "duration_ms",
    "latency_ms",
    "elapsed_ms",
    "took_ms",
)


class JsonKeysFormat:
    """Extract canonical fields from an embedded JSON object."""

    name = "json"

    def __init__(
        self,
        *,
        timestamp_keys: tuple[str, ...] = DEFAULT_TIMESTAMP_KEYS,
        level_keys: tuple[str, ...] = DEFAULT_LEVEL_KEYS,
        message_keys: tuple[str, ...] = DEFAULT_MESSAGE_KEYS,
        component_keys: tuple[str, ...] = DEFAULT_COMPONENT_KEYS,
        status_keys: tuple[str, ...] = DEFAULT_STATUS_KEYS,
        duration_ms_keys: tuple[str, ...] = DEFAULT_DURATION_MS_KEYS,
    ) -> None:
        self.timestamp_keys = timestamp_keys
        self.level_keys = level_keys
        self.message_keys = message_keys
        self.component_keys = component_keys
        self.status_keys = status_keys
        self.duration_ms_keys = duration_ms_keys

    def parse(self, raw_line: str) -> CanonicalRecord:
        cleaned = ANSI_RE.sub("", raw_line)
        rec = _parse_record_dict(cleaned)
        return CanonicalRecord(
            timestamp=self._timestamp(rec, cleaned),
            level=_first_string(rec, self.level_keys).lower(),
            message=_first_string(rec, self.message_keys),
            component=_first_string(rec, self.component_keys),
            status=self._status(rec, cleaned),
            duration_ms=self._duration_ms(rec),
            raw_line=raw_line,
        )

    def _timestamp(self, rec: dict[str, Any] | None, line: str) -> str:
        if rec:
            for key in self.timestamp_keys:
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

    def _status(self, rec: dict[str, Any] | None, line: str) -> str:
        if rec:
            for key in self.status_keys:
                value = rec.get(key)
                if isinstance(value, int | str):
                    text = str(value)
                    if text.isdigit() and len(text) == 3:
                        return text
        match = HTTP_ACCESS_RE.search(line) or STATUS_FIELD_RE.search(line)
        if match is not None:
            return match.group(1)
        return ""

    def _duration_ms(self, rec: dict[str, Any] | None) -> float | None:
        if not rec:
            return None
        for key in self.duration_ms_keys:
            value = rec.get(key)
            if isinstance(value, int | float):
                return float(value)
        # `duration` follows Go/zerolog convention: integer nanoseconds.
        # We don't infer units from magnitude — millisecond-scale values
        # would otherwise be misclassified — so the caller must use the
        # explicit ``*_ms`` keys for milliseconds.
        bare = rec.get("duration")
        if isinstance(bare, int | float):
            return float(bare) / 1_000_000
        return None


def _parse_record_dict(line: str) -> dict[str, Any] | None:
    idx = line.find("{")
    if idx == -1:
        return None
    try:
        rec = json.loads(line[idx:])
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _first_string(rec: dict[str, Any] | None, keys: tuple[str, ...]) -> str:
    if not rec:
        return ""
    for key in keys:
        value = rec.get(key)
        if value:
            return str(value)
    return ""
