"""Latency probe.

Records every ``duration_ms`` value from canonical records and reports
percentiles, mean, and the slowest requests at finalisation.
"""

from __future__ import annotations

import statistics
from typing import Any

from paperbark.probes._record import CanonicalRecord

_MIN_MS = 0.0
_MAX_MS = 3_600_000.0  # one hour; anything larger is almost certainly a parsing bug
_SLOWEST_RING = 200
_SLOWEST_KEEP = 50
_SLOWEST_REPORT = 10
_SAMPLE_TRIM = 240


class LatencyProbe:
    """Track request latency in milliseconds."""

    name = "Latency"

    def __init__(self) -> None:
        self._values: list[float] = []
        self._slowest: list[tuple[float, str, str]] = []

    def feed(self, record: CanonicalRecord) -> None:
        if record.duration_ms is None:
            return
        ms = record.duration_ms
        if ms < _MIN_MS or ms > _MAX_MS:
            return
        self._values.append(ms)
        self._slowest.append((ms, record.timestamp, record.raw_line))
        if len(self._slowest) > _SLOWEST_RING:
            self._slowest.sort(key=lambda entry: -entry[0])
            self._slowest = self._slowest[:_SLOWEST_KEEP]

    def report(self) -> dict[str, Any]:
        if not self._values:
            return {
                "name": self.name,
                "findings": [],
                "note": "no duration fields seen",
            }
        sorted_values = sorted(self._values)
        self._slowest.sort(key=lambda entry: -entry[0])
        slowest = [
            {
                "duration_ms": ms,
                "timestamp": ts,
                "line": line.strip()[:_SAMPLE_TRIM],
            }
            for ms, ts, line in self._slowest[:_SLOWEST_REPORT]
        ]
        return {
            "name": self.name,
            "samples": len(sorted_values),
            "p50_ms": _percentile(sorted_values, 50),
            "p95_ms": _percentile(sorted_values, 95),
            "p99_ms": _percentile(sorted_values, 99),
            "max_ms": sorted_values[-1],
            "mean_ms": statistics.fmean(sorted_values),
            "slowest": slowest,
        }


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the linear-interpolation percentile (Type 7 / inclusive).

    Using ``round()`` here previously hit banker's-rounding edge cases —
    p50 of [100, 300] returned 100 instead of 200. This implementation
    matches ``statistics.quantiles(method="inclusive")``.
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1 or pct <= 0:
        return float(sorted_values[0])
    if pct >= 100:
        return float(sorted_values[-1])
    rank = pct / 100 * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return float(sorted_values[lo]) + frac * (float(sorted_values[hi]) - float(sorted_values[lo]))
