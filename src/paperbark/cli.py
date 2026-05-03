"""Paperbark command-line interface.

Argparse front end and dispatch into the real subcommand implementations
as they land. ``search`` and ``monitor`` are wired through; ``analyse``
and ``init`` still hit the not-yet-implemented fallback (exit 2) until
those layers ship.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

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

    subparsers.add_parser(
        "init",
        help="Write a starter paperbark.toml in the current directory.",
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

    if command == "monitor":
        try:
            return _run_monitor(args)
        except KeyboardInterrupt:
            return 130

    sys.stderr.write(f"paperbark {__version__}: '{command}' is not yet implemented.\n")
    return _NOT_IMPLEMENTED_EXIT


def _run_monitor(args: argparse.Namespace) -> int:
    """Glue between ``cli`` argparse and the dispatcher.

    Loads the TOML config (explicit ``--config`` or discovery), runs one
    iteration, and prints the resulting run directory. Errors from the
    config and dispatcher layers surface as exit 2 with a single-line
    stderr message.
    """
    from paperbark.config import ConfigError, load
    from paperbark.dispatcher import DispatcherError, run_monitor

    # When the user invokes plain `paperbark` (no subcommand), the
    # `monitor` subparser hasn't run, so attributes like `config` aren't
    # on the namespace. Treat that case as "no overrides, use defaults".
    config_arg = getattr(args, "config", None)
    config_path = Path(config_arg) if config_arg else None
    try:
        config = load(config_path)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 2
    try:
        run_dir = run_monitor(config)
    except DispatcherError as exc:
        sys.stderr.write(f"monitor error: {exc}\n")
        return 2
    sys.stdout.write(f"run: {run_dir}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
