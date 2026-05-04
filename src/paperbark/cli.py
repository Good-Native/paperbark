"""Paperbark command-line interface.

Argparse front end and dispatch into the real subcommand implementations.
``search`` (via :mod:`paperbark.search`), ``monitor`` (via
:mod:`paperbark.dispatcher`), ``analyse`` (via :mod:`paperbark.analyse`),
and ``init`` (via :mod:`paperbark.init`) are all wired through; the
``_NOT_IMPLEMENTED_EXIT`` fallback now only catches typos in dispatch.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from paperbark import __version__

if TYPE_CHECKING:  # pragma: no cover — types only.
    from paperbark.config import MonitorConfig
    from paperbark.dispatcher import MonitorState, SnapshotRunner

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
    monitor.add_argument(
        "--interval",
        default=None,
        help="Seconds between iterations (or 30s/5m/1h). Overrides [monitor].interval.",
    )
    monitor.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Number of iterations (0 = forever). Overrides [monitor].iterations.",
    )
    monitor.add_argument(
        "--run-id",
        default=None,
        help="Run identifier (slug). Overrides [monitor].run_id; empty = auto-generate.",
    )
    monitor.add_argument(
        "--analyse-every",
        default=None,
        help=("Snapshot analyse cadence (or 0 to disable). Overrides [monitor].analyse_every."),
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
        help="'latest' (default), 'all', a date, or a run dir.",
    )
    analyse.add_argument(
        "--root",
        default="logs",
        help="Logs root directory (default: logs).",
    )
    analyse.add_argument(
        "--app",
        default="",
        help="Comma-separated app filter (default: all apps in run).",
    )
    analyse.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Ad-hoc keyword (repeatable).",
    )
    analyse.add_argument(
        "--regex",
        action="append",
        default=[],
        help="Ad-hoc regex (repeatable).",
    )
    analyse.add_argument(
        "--out",
        default=None,
        help="Override output base path; writes <out>.json + <out>.md.",
    )
    analyse.add_argument(
        "--stdout",
        action="store_true",
        help="Print the rendered markdown to stdout in addition to writing files.",
    )

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

    if command == "monitor":
        try:
            return _run_monitor(args)
        except KeyboardInterrupt:
            return 130

    if command == "analyse":
        from paperbark.analyse import run as run_analyse

        try:
            return run_analyse(args)
        except KeyboardInterrupt:
            return 130

    if command == "init":
        from paperbark.init import run as run_init

        return run_init(args)

    sys.stderr.write(f"paperbark {__version__}: '{command}' is not yet implemented.\n")
    return _NOT_IMPLEMENTED_EXIT


def _run_monitor(args: argparse.Namespace) -> int:
    """Glue between ``cli`` argparse and the long-running dispatcher loop.

    Loads the TOML config (explicit ``--config`` or discovery), merges CLI
    flag overrides into the :class:`MonitorConfig`, installs a SIGINT handler
    that flips the loop's stop flag, and runs the dispatcher's iteration loop
    until it stops naturally or the user interrupts. On a TTY we drive the
    rich-live animator; without a TTY we fall back to one progress line per
    iteration on stderr so logs and CI capture remain readable.
    """
    import signal
    import threading

    from paperbark.analyse import run as run_analyse
    from paperbark.animator import MonitorAnimator
    from paperbark.config import ConfigError, load
    from paperbark.dispatcher import DispatcherError, run_monitor_loop

    config_arg = getattr(args, "config", None)
    config_path = Path(config_arg) if config_arg else None
    try:
        config = load(config_path)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 2

    try:
        monitor_cfg = _merge_monitor_overrides(config.monitor, args)
    except ValueError as exc:
        sys.stderr.write(f"monitor error: {exc}\n")
        return 2

    snapshot_runner = _make_snapshot_runner(config.root, run_analyse)

    stop_event = threading.Event()
    previous_handler = signal.getsignal(signal.SIGINT)

    def _handle_sigint(_signum: int, _frame: object) -> None:
        # First Ctrl+C asks the loop to finish gracefully; a second one bypasses
        # this handler (it has been replaced by the default again) and exits hard.
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        stop_event.set()
        sys.stderr.write("\nstop requested — finishing current iteration...\n")

    signal.signal(signal.SIGINT, _handle_sigint)

    use_animator = sys.stdout.isatty()
    try:
        if use_animator:
            with MonitorAnimator() as ticker:
                result = run_monitor_loop(
                    config,
                    monitor=monitor_cfg,
                    stop_event=stop_event,
                    on_state=ticker.update,
                    snapshot_runner=snapshot_runner,
                )
        else:
            result = run_monitor_loop(
                config,
                monitor=monitor_cfg,
                stop_event=stop_event,
                on_state=_print_state_line,
                snapshot_runner=snapshot_runner,
            )
    except DispatcherError as exc:
        sys.stderr.write(f"monitor error: {exc}\n")
        return 2
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    sys.stdout.write(
        f"run: {result.run_dir} "
        f"({result.iterations_completed} iteration(s), "
        f"{result.captured_total} line(s) captured)\n"
    )
    if result.stopped_early:
        sys.stdout.write("stopped early on user request.\n")
    return 0


def _merge_monitor_overrides(
    base: MonitorConfig,
    args: argparse.Namespace,
) -> MonitorConfig:
    """Apply ``args`` overrides to ``base``, preserving defaults for anything unset.

    Unspecified flags come through as ``None`` (per ``default=None`` on the
    argparse spec), so we use that as the "leave alone" sentinel. Validation
    delegates to :func:`paperbark.duration.parse_duration` for the time fields
    so the same forms accepted in TOML work on the CLI.
    """
    from paperbark.config import MonitorConfig
    from paperbark.duration import parse_duration

    interval = base.interval
    iterations = base.iterations
    analyse_every = base.analyse_every
    run_id = base.run_id

    interval_arg = getattr(args, "interval", None)
    if interval_arg is not None:
        seconds = parse_duration(interval_arg)
        if seconds <= 0:
            raise ValueError("--interval must be > 0")
        interval = seconds

    iterations_arg = getattr(args, "iterations", None)
    if iterations_arg is not None:
        if iterations_arg < 0:
            raise ValueError("--iterations must be >= 0")
        iterations = iterations_arg

    analyse_every_arg = getattr(args, "analyse_every", None)
    if analyse_every_arg is not None:
        analyse_every = parse_duration(analyse_every_arg)

    run_id_arg = getattr(args, "run_id", None)
    if run_id_arg is not None:
        run_id = run_id_arg

    return MonitorConfig(
        interval=interval,
        iterations=iterations,
        analyse_every=analyse_every,
        run_id=run_id,
    )


def _make_snapshot_runner(
    root: Path,
    run_analyse: Callable[[argparse.Namespace], int],
) -> SnapshotRunner:
    """Build a :data:`SnapshotRunner` that calls into ``paperbark.analyse``.

    The dispatcher invokes this with ``(run_dir, out_base)``. ``out_base``
    selects between snapshot writes (``<run>/snapshots/analysis_<ts>``) and
    the final report (``None`` → :func:`paperbark.analyse.run` defaults to
    ``<run>/analysis``). We build an :class:`argparse.Namespace` matching the
    analyse subcommand's contract so the same code path serves both modes.
    """

    def _run(run_dir: Path, out_base: Path | None) -> None:
        rel = run_dir.relative_to(root).as_posix()
        ns = argparse.Namespace(
            run=rel,
            root=str(root),
            app="",
            keyword=[],
            regex=[],
            out=str(out_base) if out_base is not None else None,
            stdout=False,
        )
        run_analyse(ns)

    return _run


def _print_state_line(state: MonitorState) -> None:
    """Plain-text fallback for non-TTY ``on_state``: one line per publish."""
    if state.iteration == 0 and not state.finished:
        return  # initial pre-loop publish; nothing user-facing yet.
    suffix = " [done]" if state.finished else ""
    iter_part = f"{state.iteration}"
    if state.iterations_max > 0:
        iter_part = f"{state.iteration}/{state.iterations_max}"
    sys.stderr.write(
        f"iter {iter_part} elapsed={state.elapsed_seconds}s "
        f"captured={state.captured_total}{suffix}\n"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
