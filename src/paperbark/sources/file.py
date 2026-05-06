"""On-disk log file source.

Reads a single text file from disk and yields its lines. Each
:meth:`capture` call re-opens the file and streams it from the start —
the source is stateless across calls, per the project's source
contract, and the cursor filter (``paperbark.cursor``) is responsible
for cross-iteration dedup.

This means ``FileSource`` is most useful for log shapes whose lines
begin with an ISO-8601 timestamp (Fly-style JSON-with-prefix, syslog
emitted with a leading TS, custom shapes that lead with a TS): the
cursor filter advances on each iteration and only emits genuinely new
lines. For shapes without a leading ISO timestamp (Apache combined,
nginx default, RFC 5424 syslog with its ``<PRI>1`` prefix) the cursor
filter drops every line — see :file:`docs/SOURCES.md` for the matrix.
A format-aware cursor mode is on the v0.2+ list.

The ``since`` advisory parameter is silently ignored: the cursor
filter handles bounding regardless, and a file source has no upstream
query to forward it to.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paperbark.formats import Format


class FileSource:
    """Read lines from a single on-disk log file."""

    name = "file"

    def __init__(
        self,
        *,
        path: str | Path,
        encoding: str = "utf-8",
        format_keys: dict[str, tuple[str, ...]] | None = None,
        line_format: Format | None = None,
    ) -> None:
        if not path:
            raise ValueError("FileSource requires a non-empty path")
        # Existence is checked at capture time, not construction — log
        # files often appear and disappear under rotation, and a fresh
        # ``paperbark monitor`` shouldn't fail to start just because the
        # target hasn't been created yet.
        self.path = Path(path)
        self.encoding = encoding
        self.format_keys = format_keys
        """JSON key overrides for the iteration parser; see
        :class:`paperbark.sources.flyctl.FlyctlSource` for the rationale."""
        self.line_format = line_format
        """Optional :class:`paperbark.formats.Format` for non-JSON shapes."""

    def capture(self, *, since: str = "") -> Iterator[str]:
        # ``since`` is advisory and we have no upstream to forward it
        # to; cursor filtering downstream still bounds the output.
        del since
        # ``errors="replace"`` mirrors the iteration parser's tolerance:
        # we'd rather emit a mojibake'd line than drop a record because
        # of a stray byte.
        with self.path.open("r", encoding=self.encoding, errors="replace") as handle:
            yield from handle
