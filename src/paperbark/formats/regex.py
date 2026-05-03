"""Named-group regex format.

Matches ``pattern`` against each line and maps named groups to canonical
fields. Recognised groups: ``timestamp``, ``level``, ``message``,
``component``, ``status``, ``duration_ms``. Groups that are absent from
the pattern simply leave their canonical field empty.

For non-ISO timestamp formats (e.g. Apache's ``%d/%b/%Y:%H:%M:%S %z``)
pass ``ts_format`` and the regex group will be parsed via ``strptime``
before being re-emitted as ISO seconds.
"""

from __future__ import annotations

import re
from datetime import datetime

from paperbark.probes._record import CanonicalRecord

_KNOWN_GROUPS = {"timestamp", "level", "message", "component", "status", "duration_ms"}


class RegexFormat:
    """Match a named-group regex against each line."""

    def __init__(
        self,
        name: str,
        pattern: re.Pattern[str],
        *,
        ts_format: str | None = None,
        level_map: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.pattern = pattern
        self.ts_format = ts_format
        self.level_map = level_map or {}

    def parse(self, raw_line: str) -> CanonicalRecord:
        match = self.pattern.search(raw_line)
        if match is None:
            return _empty(raw_line)
        groups = {
            key: value
            for key, value in match.groupdict().items()
            if value is not None and key in _KNOWN_GROUPS
        }
        return CanonicalRecord(
            timestamp=self._timestamp(groups.get("timestamp", "")),
            level=self._level(groups.get("level", "")),
            message=groups.get("message", ""),
            component=groups.get("component", ""),
            status=self._status(groups.get("status", "")),
            duration_ms=self._duration_ms(groups.get("duration_ms", "")),
            raw_line=raw_line,
        )

    def _timestamp(self, raw: str) -> str:
        if not raw:
            return ""
        if self.ts_format is not None:
            try:
                return datetime.strptime(raw, self.ts_format).isoformat(timespec="seconds")
            except ValueError:
                return ""
        normalised = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalised).isoformat(timespec="seconds")
        except ValueError:
            return ""

    def _level(self, raw: str) -> str:
        if not raw:
            return ""
        lowered = raw.lower()
        return self.level_map.get(lowered, lowered)

    def _status(self, raw: str) -> str:
        if raw.isdigit() and len(raw) == 3:
            return raw
        return ""

    def _duration_ms(self, raw: str) -> float | None:
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


def _empty(raw_line: str) -> CanonicalRecord:
    return CanonicalRecord(
        timestamp="",
        level="",
        message="",
        component="",
        status="",
        duration_ms=None,
        raw_line=raw_line,
    )
