"""Pin every TOML example in ``docs/SOURCES.md`` to the loader.

A doc drift here is the easiest way to mislead users — they copy a sample,
the loader rejects it, and they have to debug a hand-written reference
rather than the running code. Reading every fenced ``toml`` block out of
``docs/SOURCES.md`` and feeding it through :func:`paperbark.config.from_dict`
is cheap and keeps the source-of-truth doc honest.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from paperbark.config import from_dict

SOURCES_DOC = Path(__file__).resolve().parents[1] / "docs" / "SOURCES.md"


def _extract_toml_blocks(text: str) -> list[str]:
    """Return every ```` ```toml ```` ... ```` ``` ```` block in ``text``.

    Hand-rolled rather than pulling in a markdown library so the test
    suite stays dependency-light. The format we emit is consistent
    (always ` ```toml ` on its own line; ` ``` ` to close), so a
    line-oriented scan is sufficient.
    """
    blocks: list[str] = []
    in_block = False
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "```toml":
            in_block = True
            buf = []
            continue
        if in_block and stripped == "```":
            blocks.append("\n".join(buf))
            in_block = False
            continue
        if in_block:
            buf.append(line)
    if in_block:
        raise AssertionError("unterminated ```toml fenced block in docs/SOURCES.md")
    return blocks


def test_sources_doc_has_toml_examples() -> None:
    """Sanity guard: a doc with no examples would silently pass the next test."""
    text = SOURCES_DOC.read_text(encoding="utf-8")
    assert _extract_toml_blocks(text), "no ```toml fenced blocks in docs/SOURCES.md"


_DOC_BLOCKS = list(enumerate(_extract_toml_blocks(SOURCES_DOC.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("index, block", _DOC_BLOCKS)
def test_sources_doc_example_parses(index: int, block: str) -> None:
    """Every ```toml block in ``docs/SOURCES.md`` must round-trip through the loader."""
    raw = tomllib.loads(block)
    # ``from_dict`` raises ``ConfigError`` (a ValueError subclass) on shape
    # / semantic violations; pytest will surface the message verbatim with
    # the failing block index pinned by parametrize.
    from_dict(raw)
