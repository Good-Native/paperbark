"""Standard-input log source.

Reads lines from ``sys.stdin`` and yields them through the rest of the
pipeline. The intended use is piping pre-captured logs into a one-shot
``paperbark monitor`` / ``analyse`` / ``search`` run, e.g.::

    cat app.log | paperbark monitor --iterations 1

The source is stateless across calls in the spirit of the source
contract — but ``sys.stdin`` is itself a single-use stream owned by
the parent process. After EOF, subsequent :meth:`capture` calls yield
nothing rather than re-raising; long-running monitor loops over a
piped stdin therefore see one productive iteration followed by empty
ones, which matches the typical one-shot use.

Encoding is whatever Python wired ``sys.stdin`` to at process start
(``PYTHONIOENCODING`` and the system locale settle this). For
byte-level robustness or a custom encoding, prefer the ``file``
source — it owns the underlying handle and can apply
``errors="replace"`` safely.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from paperbark.formats import Format


class StdinSource:
    """Read lines from standard input."""

    name = "stdin"

    def __init__(
        self,
        *,
        format_keys: dict[str, tuple[str, ...]] | None = None,
        line_format: Format | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.format_keys = format_keys
        """JSON key overrides for the iteration parser; see
        :class:`paperbark.sources.flyctl.FlyctlSource` for the rationale."""
        self.line_format = line_format
        """Optional :class:`paperbark.formats.Format` for non-JSON shapes."""
        # The ``stream`` kwarg is the test seam — pass a ``StringIO`` to
        # avoid touching the real ``sys.stdin``. Not surfaced in TOML; the
        # dispatcher never forwards it.
        self._stream = stream

    def capture(self, *, since: str = "") -> Iterator[str]:
        # ``since`` is advisory and stdin has no upstream to forward it
        # to; cursor filtering downstream still bounds the output.
        del since
        stream = self._stream if self._stream is not None else sys.stdin
        # Yield lazily; do not close — ``sys.stdin`` is owned by the
        # parent process, and an injected test stream is owned by the
        # caller.
        yield from stream
