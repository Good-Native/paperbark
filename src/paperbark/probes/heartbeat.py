"""Heartbeat probe.

Detects minutes within a run where info-level traffic dropped to zero
mid-flight — a reliable signal that an app stopped emitting healthy
chatter even when it didn't crash outright.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from paperbark.probes._record import CanonicalRecord, iso_minute

_GAP_REPORT_LIMIT = 200
_GAP_DISPLAY_LIMIT = 20


class HeartbeatProbe:
    """Find minutes with zero info-level traffic between observed minutes."""

    name = "Heartbeat"

    def __init__(self) -> None:
        self._minute_info: Counter[str] = Counter()
        self._minute_seen: list[str] = []

    def feed(self, record: CanonicalRecord) -> None:
        if not record.timestamp:
            return
        minute = iso_minute(record.timestamp)
        if not minute:
            return
        if not self._minute_seen or self._minute_seen[-1] != minute:
            self._minute_seen.append(minute)
        if record.level == "info":
            self._minute_info[minute] += 1

    def report(self) -> dict[str, Any]:
        if not self._minute_seen:
            return {
                "name": self.name,
                "findings": [],
                "note": "no timestamped traffic",
            }
        minutes = sorted(set(self._minute_seen))
        non_zero = [
            self._minute_info[minute] for minute in minutes if self._minute_info.get(minute, 0) > 0
        ]
        median = statistics.median(non_zero) if non_zero else 0
        gaps: set[str] = set()
        # Two ways a minute is a heartbeat gap:
        #   (1) observed in minute_seen (some traffic) but zero info, or
        #   (2) entirely missing — fell between two observed minutes with
        #       no log lines, so minute_seen never recorded it.
        if median >= 1:
            # Skip the first and last observed minutes; they are typically
            # partial windows (capture started or stopped mid-minute), so
            # zero info there is expected, not a real gap.
            for minute in minutes[1:-1]:
                if self._minute_info.get(minute, 0) == 0:
                    gaps.add(minute)
            if len(minutes) >= 2:
                for prev, curr in pairwise(minutes):
                    try:
                        prev_dt = datetime.strptime(prev, "%Y-%m-%dT%H:%M")
                        curr_dt = datetime.strptime(curr, "%Y-%m-%dT%H:%M")
                    except ValueError:
                        continue
                    step = prev_dt + timedelta(minutes=1)
                    while step < curr_dt:
                        gaps.add(step.strftime("%Y-%m-%dT%H:%M"))
                        step += timedelta(minutes=1)
        gap_findings = [
            {"minute": minute, "expected_min": int(median)}
            for minute in sorted(gaps)[:_GAP_REPORT_LIMIT]
        ]
        return {
            "name": self.name,
            "median_info_per_minute": median,
            "first_minute": minutes[0],
            "last_minute": minutes[-1],
            "gap_minutes": gap_findings[:_GAP_DISPLAY_LIMIT],
        }
