"""Bucket: count + window + peak-minute tracker for one finding label."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from paperbark.probes._record import iso_minute

_SAMPLE_LIMIT = 3
_SAMPLE_TRIM = 240


@dataclass(frozen=True, slots=True)
class Finding:
    """The shape every probe emits for each label.

    The ``{count, first_seen, last_seen, peak}`` keys are the project
    contract; ``peak_count`` and ``samples`` are conveniences for the
    reporter and JSON consumers.
    """

    label: str
    count: int
    first_seen: str
    last_seen: str
    peak: str
    peak_count: int
    samples: tuple[str, ...]


class Bucket:
    """Accumulator for one finding label.

    Tracks total count, first-seen and last-seen timestamps, the
    peak-minute and its count, and up to three short sample lines for
    operator context.
    """

    __slots__ = ("_minute_counts", "_samples", "count", "first", "last")

    def __init__(self) -> None:
        self.count: int = 0
        self.first: str = ""
        self.last: str = ""
        self._minute_counts: Counter[str] = Counter()
        self._samples: list[str] = []

    def add(self, timestamp: str, sample: str | None = None) -> None:
        """Record one occurrence at ``timestamp`` with optional ``sample``."""
        self.count += 1
        if timestamp:
            if not self.first or timestamp < self.first:
                self.first = timestamp
            if not self.last or timestamp > self.last:
                self.last = timestamp
            self._minute_counts[iso_minute(timestamp)] += 1
        if sample is not None and len(self._samples) < _SAMPLE_LIMIT:
            trimmed = sample.strip()
            if len(trimmed) > _SAMPLE_TRIM:
                trimmed = trimmed[: _SAMPLE_TRIM - 3] + "..."
            if trimmed:
                self._samples.append(trimmed)

    def to_finding(self, label: str) -> Finding:
        """Snapshot the bucket as a :class:`Finding`."""
        peak_minute, peak_count = "", 0
        if self._minute_counts:
            peak_minute, peak_count = self._minute_counts.most_common(1)[0]
        return Finding(
            label=label,
            count=self.count,
            first_seen=self.first,
            last_seen=self.last,
            peak=peak_minute,
            peak_count=peak_count,
            samples=tuple(self._samples),
        )
