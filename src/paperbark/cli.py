"""Paperbark command-line interface.

This is a scaffold. Subcommands print a "not yet implemented" notice and
exit non-zero so callers (and CI smoke tests) can tell scaffolding apart
from a real implementation. Wire up real behaviour as the source, format,
and probe layers land.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from paperbark import __version__

_NOT_IMPLEMENTED_EXIT = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperbark",
        description="Configurable cross-source log capture, search, and analysis CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"paperbark {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    monitor = subparsers.add_parser(
        "monitor",
        help="Capture logs from configured sources and run probes (default).",
    )
    monitor.add_argument(
        "--config",
        default=None,
        help="Path to a paperbark.toml; overrides discovery.",
    )

    search = subparsers.add_parser(
        "search",
        help="Search across captured runs.",
    )
    search.add_argument("--keyword", help="Plain-text keyword to match.")
    search.add_argument("--regex", help="Regular expression to match.")
    search.add_argument(
        "--run",
        default="latest",
        help="Run selector: 'latest', 'all', or a run id.",
    )

    analyse = subparsers.add_parser(
        "analyse",
        help="Re-run analysis over an existing capture.",
    )
    analyse.add_argument(
        "--run",
        default="latest",
        help="Run selector: 'latest', 'all', or a run id.",
    )
    analyse.add_argument("--keyword", help="Optional keyword filter.")
    analyse.add_argument("--regex", help="Optional regex filter.")

    subparsers.add_parser(
        "init",
        help="Write a starter paperbark.toml in the current directory.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    command = args.command or "monitor"

    sys.stderr.write(f"paperbark {__version__}: '{command}' is not yet implemented.\n")
    return _NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
