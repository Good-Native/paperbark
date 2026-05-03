"""Panic / fatal probe."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from paperbark.probes._bucket import Bucket
from paperbark.probes._record import CanonicalRecord

_PANIC_RE = re.compile(r"panic:\s*(.+)", re.IGNORECASE)
_FATAL_RE = re.compile(r"\bfatal(?:\s+error)?:\s*(.+)", re.IGNORECASE)
_KEY_TRIM = 120
_TOP_N = 10


class PanicProbe:
    """Bucket panic / fatal lines by their first-line cause."""

    name = "Panics & fatals"

    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = defaultdict(Bucket)

    def feed(self, record: CanonicalRecord) -> None:
        for pattern, kind in ((_PANIC_RE, "panic"), (_FATAL_RE, "fatal")):
            match = pattern.search(record.raw_line)
            if match is None:
                continue
            rest = match.group(1).strip()
            key = rest.split("\n", 1)[0][:_KEY_TRIM] or kind
            self._buckets[f"{kind}: {key}"].add(record.timestamp, record.raw_line)
            return

    def report(self) -> dict[str, Any]:
        findings = sorted(
            (asdict(bucket.to_finding(label)) for label, bucket in self._buckets.items()),
            key=lambda f: -f["count"],
        )
        return {"name": self.name, "findings": findings[:_TOP_N]}
