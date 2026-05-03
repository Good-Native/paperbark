"""Probe layer.

Every probe consumes a :class:`CanonicalRecord` (no source-specific
branching) and produces findings of the shape
``{count, first_seen, last_seen, peak, ...}`` per the project contract.
Each probe class is opt-out via TOML config (the loader for that lands
with the config layer; for now :func:`default_probes` returns the full
set).
"""

from __future__ import annotations

import re

from paperbark.probes._base import Probe
from paperbark.probes._bucket import Bucket, Finding
from paperbark.probes._record import CanonicalRecord, parse_line
from paperbark.probes.heartbeat import HeartbeatProbe
from paperbark.probes.http import HTTPStatusProbe
from paperbark.probes.latency import LatencyProbe
from paperbark.probes.panic import PanicProbe
from paperbark.probes.regex_bucket import RegexBucketProbe
from paperbark.probes.severity import SeverityProbe

__all__ = [
    "Bucket",
    "CanonicalRecord",
    "Finding",
    "HTTPStatusProbe",
    "HeartbeatProbe",
    "LatencyProbe",
    "PanicProbe",
    "Probe",
    "RegexBucketProbe",
    "SeverityProbe",
    "default_probes",
    "parse_line",
]


def default_probes(
    extra_keywords: list[str] | None = None,
    extra_regexes: list[str] | None = None,
) -> list[Probe]:
    """Return the default probe set in the order findings are reported.

    Once the config layer lands, probes will be filtered by the TOML
    ``[probes]`` table; for now this returns every built-in probe.
    """
    probes: list[Probe] = [
        SeverityProbe(),
        PanicProbe(),
        HTTPStatusProbe(),
        LatencyProbe(),
        HeartbeatProbe(),
        RegexBucketProbe(
            "Process health",
            [
                ("starting machine", r"starting machine"),
                ("stopping machine", r"stopping machine"),
                ("exited with code", r"exited with code\s+\d+"),
                ("out of memory", r"out of memory|oom[- ]?killed"),
                ("killed by signal", r"killed by signal|signal:\s*killed"),
                ("health check failed", r"health check.*fail"),
                ("restart", r"\brestart(ing)?\b"),
            ],
        ),
        RegexBucketProbe(
            "Autoscaler",
            [
                ("reconciling", r'"msg":\s*"reconciling"|reconciling\s+app'),
                ("scale up", r"scal(e|ing)\s*up|adding machine"),
                ("scale down", r"scal(e|ing)\s*down|removing machine"),
                ("target=N", r"target\s*[=:]\s*\d+|\"target\":\s*\{"),
                ("queue depth", r"queue[_ ]depth|backlog\s*[=:]\s*\d+"),
                ("no-op", r"no scale change|already at target"),
            ],
        ),
        RegexBucketProbe(
            "Database / external",
            [
                ("pgx error", r"\bpgx\b.*error|pgx:.*"),
                ("pq error", r"\bpq:\s"),
                ("connection refused", r"connection refused"),
                ("context deadline exceeded", r"context deadline exceeded"),
                ("i/o timeout", r"i/o timeout"),
                ("connection reset", r"connection reset"),
                ("too many connections", r"too many connections"),
            ],
        ),
        RegexBucketProbe(
            "Sentry",
            [
                ("event sent", r"sentry.*event\b|event sent to sentry"),
                ("send failed", r"sentry.*(?:fail|error)"),
            ],
        ),
    ]
    keywords = extra_keywords or []
    regexes = extra_regexes or []
    if keywords or regexes:
        adhoc: list[tuple[str, str]] = [(f"keyword:{k}", re.escape(k)) for k in keywords]
        adhoc += [(f"regex:{r}", r) for r in regexes]
        probes.append(RegexBucketProbe("Ad-hoc keywords", adhoc))
    return probes
