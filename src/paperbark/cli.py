"""Paperbark command-line interface.

Argparse front end and dispatch into the real subcommand implementations
as they land. ``search`` (via :mod:`paperbark.search`) and ``init`` (via
:mod:`paperbark.init`) are wired through; ``monitor`` and ``analyse``
still hit the not-yet-implemented fallback (exit 2) until the dispatcher
lands.
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
    search.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Literal substring (repeatable).",
    )
    search.add_argument(
        "--regex",
        action="append",
        default=[],
        help="Regex pattern (repeatable).",
    )
    search.add_argument(
        "--app",
        default="",
        help="Comma-separated app filter (default: all apps in run).",
    )
    search.add_argument(
        "--run",
        default=None,
        help="'latest' (default), 'all', a date, or a run dir.",
    )
    search.add_argument(
        "--root",
        default="logs",
        help="Logs root directory (default: logs).",
    )
    search.add_argument(
        "-i",
        "--ignore-case",
        action="store_true",
        default=True,
        help="Match case-insensitively (default: on).",
    )
    search.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Force case-sensitive matching (overrides --ignore-case).",
    )
    search.add_argument(
        "--max",
        type=int,
        default=0,
        help="Stop after N total matches (0 = unlimited).",
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

    init = subparsers.add_parser(
        "init",
        help="Write a starter paperbark.toml in the current directory.",
    )
    init.add_argument(
        "--path",
        default="paperbark.toml",
        help="Output path (default: paperbark.toml).",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    command = args.command or "monitor"

    if command == "search":
        from paperbark.search import run as run_search

        try:
            return run_search(args)
        except KeyboardInterrupt:
            return 130

    if command == "init":
        from paperbark.init import run as run_init

        return run_init(args)

    sys.stderr.write(f"paperbark {__version__}: '{command}' is not yet implemented.\n")
    return _NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
