"""Probe layer.

Every probe consumes a :class:`CanonicalRecord` (no source-specific
branching) and produces findings of the shape
``{count, first_seen, last_seen, peak, ...}`` per the project contract.
:func:`default_probes` honours :class:`paperbark.config.ProbesConfig`:
each toggle drops a probe from the returned set, ad-hoc keywords and
regexes are folded into the trailing ``Ad-hoc keywords`` bucket, and
``[probes.patterns]`` entries replace the built-in regex set for the
named probe.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from paperbark.probes._base import Probe
from paperbark.probes._bucket import Bucket, Finding
from paperbark.probes._record import CanonicalRecord, parse_line
from paperbark.probes.heartbeat import HeartbeatProbe
from paperbark.probes.http import HTTPStatusProbe
from paperbark.probes.latency import LatencyProbe
from paperbark.probes.panic import PanicProbe
from paperbark.probes.regex_bucket import RegexBucketProbe
from paperbark.probes.severity import SeverityProbe

if TYPE_CHECKING:
    from paperbark.config import ProbesConfig

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

_PROCESS_HEALTH_PATTERNS: list[tuple[str, str]] = [
    ("starting machine", r"starting machine"),
    ("stopping machine", r"stopping machine"),
    ("exited with code", r"exited with code\s+\d+"),
    ("out of memory", r"out of memory|oom[- ]?killed"),
    ("killed by signal", r"killed by signal|signal:\s*killed"),
    ("health check failed", r"health check.*fail"),
    ("restart", r"\brestart(ing)?\b"),
]
_AUTOSCALER_PATTERNS: list[tuple[str, str]] = [
    ("reconciling", r'"msg":\s*"reconciling"|reconciling\s+app'),
    ("scale up", r"scal(e|ing)\s*up|adding machine"),
    ("scale down", r"scal(e|ing)\s*down|removing machine"),
    ("target=N", r"target\s*[=:]\s*\d+|\"target\":\s*\{"),
    ("queue depth", r"queue[_ ]depth|backlog\s*[=:]\s*\d+"),
    ("no-op", r"no scale change|already at target"),
]
_DATABASE_PATTERNS: list[tuple[str, str]] = [
    ("pgx error", r"\bpgx\b.*error|pgx:.*"),
    ("pq error", r"\bpq:\s"),
    ("connection refused", r"connection refused"),
    ("context deadline exceeded", r"context deadline exceeded"),
    ("i/o timeout", r"i/o timeout"),
    ("connection reset", r"connection reset"),
    ("too many connections", r"too many connections"),
]
_SENTRY_PATTERNS: list[tuple[str, str]] = [
    ("event sent", r"sentry.*event\b|event sent to sentry"),
    ("send failed", r"sentry.*(?:fail|error)"),
]

# Display-name kept beside each toggle so config-driven filtering can stay in
# one place. The order is the report order — keep changes here in sync with
# README and docs/PROBES.md.
_REGEX_PROBES: tuple[tuple[str, str, list[tuple[str, str]]], ...] = (
    ("process_health", "Process health", _PROCESS_HEALTH_PATTERNS),
    ("autoscaler", "Autoscaler", _AUTOSCALER_PATTERNS),
    ("database", "Database / external", _DATABASE_PATTERNS),
    ("sentry", "Sentry", _SENTRY_PATTERNS),
)


def default_probes(
    extra_keywords: list[str] | None = None,
    extra_regexes: list[str] | None = None,
    *,
    config: ProbesConfig | None = None,
) -> list[Probe]:
    """Return the default probe set in the order findings are reported.

    ``config`` (when supplied) drives:

    - which built-in probes are included (toggles under ``[probes]``);
    - whether built-in regex sets are replaced by ``[probes.patterns]``
      overrides for that probe (overrides replace, they do not extend —
      copy the defaults across if you want to extend);
    - ad-hoc keywords/regexes folded into the trailing ``Ad-hoc keywords``
      bucket alongside ``extra_keywords`` / ``extra_regexes``.
    """
    cfg = _resolve_config(config)
    probes: list[Probe] = []
    if cfg.is_enabled("severity"):
        probes.append(SeverityProbe())
    if cfg.is_enabled("panics"):
        probes.append(PanicProbe())
    if cfg.is_enabled("http"):
        probes.append(HTTPStatusProbe())
    if cfg.is_enabled("latency"):
        probes.append(LatencyProbe())
    if cfg.is_enabled("heartbeat"):
        probes.append(HeartbeatProbe())

    overrides = cfg.pattern_overrides
    for toggle, display, patterns in _REGEX_PROBES:
        if not cfg.is_enabled(toggle):
            continue
        override = overrides.get(toggle)
        chosen = [(o.label, o.pattern) for o in override] if override is not None else patterns
        probes.append(RegexBucketProbe(display, chosen))

    keywords = list(cfg.keywords) + list(extra_keywords or [])
    regexes = list(cfg.regexes) + list(extra_regexes or [])
    if keywords or regexes:
        adhoc: list[tuple[str, str]] = [(f"keyword:{k}", re.escape(k)) for k in keywords]
        adhoc += [(f"regex:{r}", r) for r in regexes]
        probes.append(RegexBucketProbe("Ad-hoc keywords", adhoc))
    return probes


def _resolve_config(config: ProbesConfig | None) -> ProbesConfig:
    if config is not None:
        return config
    # Lazy import keeps ``paperbark.probes`` importable from contexts that
    # don't need the config layer (probe unit tests, the format adapter
    # layer, etc.) without a hard dependency on ``paperbark.config``.
    from paperbark.config import ProbesConfig

    return ProbesConfig()
