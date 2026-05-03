"""Format layer.

Formats turn a raw log line into a :class:`CanonicalRecord` so that
downstream probes never have to branch on source-specific shapes.
v1 ships two implementations:

- :class:`JsonKeysFormat` — extracts canonical fields from the JSON
  object embedded in each line, with configurable key priority lists.
- :class:`RegexFormat` — matches a named-group regex against the line,
  with optional strptime format for non-ISO timestamps.

Three presets are bundled (:func:`apache_combined`, :func:`nginx_default`,
:func:`syslog_rfc5424`); operators can name them in TOML or supply
their own ``RegexFormat`` for bespoke shapes.

``CanonicalRecord`` lives in ``paperbark.probes._record`` for now;
that internal coupling will be cleaned up when the config layer lands.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from paperbark.formats.json_keys import JsonKeysFormat
from paperbark.formats.presets import (
    apache_combined,
    nginx_default,
    syslog_rfc5424,
)
from paperbark.formats.regex import RegexFormat
from paperbark.probes._record import CanonicalRecord


@runtime_checkable
class Format(Protocol):
    """Per-line parser producing :class:`CanonicalRecord`.

    Implementations must be stateless: the same line parsed twice must
    produce equal records, and a format instance may be shared across
    threads. Output is the canonical shape, so probes consume any
    format identically.
    """

    name: str

    def parse(self, raw_line: str) -> CanonicalRecord:
        """Return the canonical record extracted from ``raw_line``."""
        ...


__all__ = [
    "CanonicalRecord",
    "Format",
    "JsonKeysFormat",
    "RegexFormat",
    "apache_combined",
    "nginx_default",
    "registered_formats",
    "syslog_rfc5424",
]


def registered_formats() -> dict[str, Format]:
    """Return the mapping of preset name → format for config lookup."""
    presets: list[Format] = [
        JsonKeysFormat(),
        apache_combined(),
        nginx_default(),
        syslog_rfc5424(),
    ]
    return {preset.name: preset for preset in presets}
