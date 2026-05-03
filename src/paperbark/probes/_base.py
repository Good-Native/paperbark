"""Probe protocol.

Probes are stateful accumulators: ``feed`` once per record, then ``report``
once at the end. Implementations live one-per-file under this package.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from paperbark.probes._record import CanonicalRecord


@runtime_checkable
class Probe(Protocol):
    """Stateful per-line accumulator that emits a JSON-serialisable report.

    Implementations must be cheap to instantiate, hold no I/O resources,
    and tolerate the same probe being fed records out of timestamp order
    (the dispatcher has already cursor-filtered, but per-iteration order
    is not strictly monotonic across multi-app captures).
    """

    name: str

    def feed(self, record: CanonicalRecord) -> None:
        """Process one canonical record."""
        ...

    def report(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of everything seen so far."""
        ...
