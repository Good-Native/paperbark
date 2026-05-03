"""HTTP status probe.

Rolls up status codes by class (``2xx`` / ``3xx`` / ``4xx`` / ``5xx``) and
also keeps explicit per-code buckets for the codes operators most often
need to triage on (rate limiting, client cancellation, server-side
failures).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from paperbark.probes._bucket import Bucket
from paperbark.probes._record import CanonicalRecord

_INTERESTING_CODES = frozenset({"429", "499", "500", "502", "503", "504"})


class HTTPStatusProbe:
    name = "HTTP status"

    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = defaultdict(Bucket)

    def feed(self, record: CanonicalRecord) -> None:
        code = record.status
        if not code:
            return
        klass = f"{code[0]}xx"
        self._buckets[klass].add(record.timestamp)
        if code in _INTERESTING_CODES:
            self._buckets[code].add(record.timestamp, record.raw_line)

    def report(self) -> dict[str, Any]:
        findings = [
            asdict(bucket.to_finding(label))
            for label, bucket in sorted(self._buckets.items())
            if bucket.count
        ]
        return {"name": self.name, "findings": findings}
