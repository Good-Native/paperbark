"""Source abstraction.

Every log source implements the :class:`Source` Protocol: ``capture()``
yields raw lines and the source must not retain state between calls.
The mandatory cursor filter (``paperbark.cursor``) is applied
externally by the dispatcher — sources should never try to deduplicate
their own output.

Only the flyctl source is real in v1. The others are stubs that
satisfy the Protocol but raise on ``capture()``; they exist so the
config layer can name them and tests can confirm the registry shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from paperbark.sources.cloudwatch import CloudWatchSource
from paperbark.sources.file import FileSource
from paperbark.sources.flyctl import FlyctlSource
from paperbark.sources.kubectl import KubectlSource
from paperbark.sources.stdin import StdinSource
from paperbark.sources.wrangler import WranglerSource


@runtime_checkable
class Source(Protocol):
    """A log source.

    Implementations capture raw log lines from one upstream system per
    instance. ``capture()`` returns an iterator that the dispatcher
    drains in order; the source must not buffer or retain output for a
    later call.
    """

    name: str

    def capture(self, *, since: str = "") -> Iterator[str]:
        """Yield raw log lines.

        ``since`` is an advisory ISO timestamp the source may pass to
        the upstream tool when it supports a native ``--since`` flag.
        Cursor filtering is still mandatory downstream — ``since`` only
        bounds the upstream query, not the output contract.
        """
        ...


__all__ = [
    "CloudWatchSource",
    "FileSource",
    "FlyctlSource",
    "KubectlSource",
    "Source",
    "StdinSource",
    "WranglerSource",
    "registered_sources",
]


def registered_sources() -> dict[str, type[Source]]:
    """Return the mapping of source name → class for config lookup.

    Stubs are included so a config that names ``wrangler`` or
    ``kubectl`` resolves at parse time even though calling
    ``capture()`` will raise.
    """
    return {
        FlyctlSource.name: FlyctlSource,
        WranglerSource.name: WranglerSource,
        KubectlSource.name: KubectlSource,
        CloudWatchSource.name: CloudWatchSource,
        FileSource.name: FileSource,
        StdinSource.name: StdinSource,
    }
