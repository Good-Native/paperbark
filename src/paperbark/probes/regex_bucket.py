"""Generic regex-bucketing probe.

Used as the implementation of every "match these patterns and bucket per
label" probe (Process health, Autoscaler, Database / external, Sentry,
ad-hoc keywords). One instance per probe label set, instantiated by the
factory in :mod:`paperbark.probes`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from paperbark.probes._bucket import Bucket
from paperbark.probes._record import CanonicalRecord


class RegexBucketProbe:
    """Match a list of (label, pattern) entries against the raw line.

    A line that matches multiple labels is recorded under each — that
    matches the original Hover behaviour and lets the same line surface
    in several useful rollups (e.g. an autoscaler error that also names
    a database).
    """

    def __init__(
        self,
        name: str,
        patterns: list[tuple[str, str]],
        flags: int = re.IGNORECASE,
    ) -> None:
        self.name = name
        self._compiled: list[tuple[str, re.Pattern[str]]] = [
            (label, re.compile(pattern, flags)) for label, pattern in patterns
        ]
        self._buckets: dict[str, Bucket] = defaultdict(Bucket)

    def feed(self, record: CanonicalRecord) -> None:
        for label, pattern in self._compiled:
            if pattern.search(record.raw_line):
                self._buckets[label].add(record.timestamp, record.raw_line)

    def report(self) -> dict[str, Any]:
        findings = [
            asdict(bucket.to_finding(label))
            for label, bucket in self._buckets.items()
            if bucket.count
        ]
        findings.sort(key=lambda f: -f["count"])
        return {"name": self.name, "findings": findings}
