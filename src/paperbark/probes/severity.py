"""Severity rollup probe."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from paperbark.probes._bucket import Bucket
from paperbark.probes._record import CanonicalRecord


class SeverityProbe:
    """Count records per severity level.

    Known levels are reported in canonical order; any non-empty level
    that is not in the known set rolls up under ``unknown-level`` so
    typos and bespoke severities are visible without polluting the
    main rollup.
    """

    name = "Severity"
    LEVELS = ("debug", "info", "warn", "error", "fatal")

    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = {level: Bucket() for level in self.LEVELS}
        self._unknown: Bucket = Bucket()

    def feed(self, record: CanonicalRecord) -> None:
        bucket = self._buckets.get(record.level)
        if bucket is not None:
            bucket.add(record.timestamp, record.message)
        elif record.level:
            self._unknown.add(record.timestamp, record.raw_line)

    def report(self) -> dict[str, Any]:
        findings = [
            asdict(self._buckets[level].to_finding(level))
            for level in self.LEVELS
            if self._buckets[level].count
        ]
        if self._unknown.count:
            findings.append(asdict(self._unknown.to_finding("unknown-level")))
        return {"name": self.name, "findings": findings}
