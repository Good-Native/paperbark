"""Stdin stub source.

Conforms to the :class:`paperbark.sources.Source` Protocol so the
config layer can name it, but ``capture()`` raises until the real
implementation lands.
"""

from __future__ import annotations

from collections.abc import Iterator


class StdinSource:
    name = "stdin"

    def capture(self, *, since: str = "") -> Iterator[str]:
        raise NotImplementedError("stdin source is not yet implemented")
