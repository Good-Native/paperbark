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
    from paperbark.config import (
        AnalyseConfig,
        Config,
        MonitorConfig,
        ProbesConfig,
        SearchConfig,
    )
    from paperbark.dispatcher import MonitorStart, MonitorState, SnapshotRunner

_NOT_IMPLEMENTED_EXIT = 2


def _build_parser() -> argparse.ArgumentParser:
    # Auto-update flags share a parent parser so they're valid both before
    # the subcommand (`paperbark -y monitor`) and after (`paperbark monitor
    # -y`). argparse only routes flags to the parser they're declared on,
    # so without the parents= attachment the post-subcommand form would
    # die with "unrecognized arguments".
    autoupdate_flags = argparse.ArgumentParser(add_help=False)
    autoupdate_flags.add_argument(
        "--no-auto-update",
        dest="auto_update",
        action="store_false",
        default=None,
        help="Skip the PyPI version check and upgrade prompt for this run.",
    )
    autoupdate_flags.add_argument(
        "-y",
        "--yes",
        dest="assume_yes",
        action="store_true",
        default=False,
        help="Auto-accept the upgrade prompt without asking.",
    )

    parser = argparse.ArgumentParser(
        prog="paperbark",
        description="Configurable cross-source log capture, search, and analysis CLI.",
        parents=[autoupdate_flags],
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
        parents=[autoupdate_flags],
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
    cleanup_group = monitor.add_mutually_exclusive_group()
    cleanup_group.add_argument(
        "--no-cleanup",
        dest="cleanup_enabled",
        action="store_false",
        default=None,
        help="Disable rotation of older run dirs. Overrides [monitor].cleanup_enabled.",
    )
    cleanup_group.add_argument(
        "--cleanup",
        dest="cleanup_enabled",
        action="store_true",
        default=None,
        help="Force rotation of older run dirs. Overrides [monitor].cleanup_enabled.",
    )
    monitor.add_argument(
        "--cleanup-days",
        type=int,
        default=None,
        help=(
            "Rotate run dirs older than N days (default: 1). 0 = clean every"
            " older run, including yesterday's. Overrides [monitor].cleanup_days."
        ),
    )
    monitor.add_argument(
        "--cleanup-mode",
        choices=("zip", "delete"),
        default=None,
        help=(
            "Rotation mode: 'zip' archives raw/ and removes per-iter JSON/CSV"
            " (default), 'delete' removes the run dir entirely."
            " Overrides [monitor].cleanup_mode."
        ),
    )

    search = subparsers.add_parser(
        "search",
        help="Search across captured runs.",
        parents=[autoupdate_flags],
    )
    search.add_argument(
        "--config",
        default=None,
        help="Path to a paperbark.toml; overrides discovery.",
    )
    search.add_argument(
        "--keyword",
        action="append",
        default=None,
        help="Literal substring (repeatable). Overrides [search].keywords.",
    )
    search.add_argument(
        "--regex",
        action="append",
        default=None,
        help="Regex pattern (repeatable). Overrides [search].regexes.",
    )
    search.add_argument(
        "--app",
        default=None,
        help="Comma-separated app filter. Overrides [search].app.",
    )
    search.add_argument(
        "--run",
        default=None,
        help="'latest' (default), 'all', a date, or a run dir. Overrides [search].run.",
    )
    search.add_argument(
        "--root",
        default=None,
        help="Logs root directory. Overrides [paperbark].root.",
    )
    # ``--ignore-case`` and ``--case-sensitive`` are now mutually exclusive and
    # share the ``case_sensitive`` dest. Pre-PR ``--ignore-case`` only set
    # ``args.ignore_case`` while ``paperbark.search.run`` consulted
    # ``args.case_sensitive`` exclusively, so the flag was silently inert.
    # That latent bug became user-visible once ``[search].case_sensitive``
    # landed in the TOML loader: a TOML ``true`` plus a CLI ``--ignore-case``
    # would have left case-sensitivity stuck on. The mutually exclusive group
    # makes both flags participate in the same override path; ``default=None``
    # on the parser keeps them distinguishable from explicit ``False``.
    case_group = search.add_mutually_exclusive_group()
    case_group.add_argument(
        "-i",
        "--ignore-case",
        dest="case_sensitive",
        action="store_false",
        help="Match case-insensitively. Overrides [search].case_sensitive.",
    )
    case_group.add_argument(
        "--case-sensitive",
        dest="case_sensitive",
        action="store_true",
        help="Force case-sensitive matching. Overrides [search].case_sensitive.",
    )
    search.set_defaults(case_sensitive=None)
    search.add_argument(
        "--max",
        type=int,
        default=None,
        help="Stop after N total matches (0 = unlimited). Overrides [search].max.",
    )
    # ``BooleanOptionalAction`` so a TOML ``[search].keep_ansi = true`` can
    # be cleared at the CLI with ``--no-keep-ansi``. ``default=None`` keeps
    # "flag absent" distinguishable from "explicit False" so the merge step
    # falls through to TOML.
    search.add_argument(
        "--keep-ansi",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Preserve ANSI escape sequences in matched lines. By default they"
            " are stripped so piped/redirected output stays readable;"
            " --no-keep-ansi clears a [search].keep_ansi = true."
        ),
    )

    analyse = subparsers.add_parser(
        "analyse",
        help="Re-run analysis over an existing capture.",
        parents=[autoupdate_flags],
    )
    analyse.add_argument(
        "--config",
        default=None,
        help="Path to a paperbark.toml; overrides discovery.",
    )
    analyse.add_argument(
        "--run",
        default=None,
        help="'latest' (default), 'all', a date, or a run dir. Overrides [analyse].run.",
    )
    analyse.add_argument(
        "--root",
        default=None,
        help="Logs root directory. Overrides [paperbark].root.",
    )
    analyse.add_argument(
        "--app",
        default=None,
        help="Comma-separated app filter. Overrides [analyse].app.",
    )
    analyse.add_argument(
        "--keyword",
        action="append",
        default=None,
        help="Ad-hoc keyword (repeatable). Overrides [analyse].keywords.",
    )
    analyse.add_argument(
        "--regex",
        action="append",
        default=None,
        help="Ad-hoc regex (repeatable). Overrides [analyse].regexes.",
    )
    analyse.add_argument(
        "--out",
        default=None,
        help="Override output base path; writes <out>.json + <out>.md.",
    )
    # ``BooleanOptionalAction`` (3.9+) gives us ``--stdout`` and ``--no-stdout``
    # off a single dest. Without the negative form a TOML
    # ``[analyse].stdout = true`` could only be re-affirmed at the CLI, never
    # cleared — which violates the documented "flags override TOML at
    # runtime" contract. ``default=None`` keeps "flag absent" distinguishable
    # from "explicit False" so the merge step falls through to TOML.
    analyse.add_argument(
        "--stdout",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Also print the rendered markdown to stdout. Overrides"
            " [analyse].stdout; use --no-stdout to clear a TOML true."
        ),
    )

    init = subparsers.add_parser(
        "init",
        help="Write a starter paperbark.toml in the current directory.",
        parents=[autoupdate_flags],
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
    # Detection is on by default and only takes effect when the CWD
    # has a fly.toml or wrangler.{toml,jsonc} — empty dirs see no
    # behaviour change. ``--no-detect`` is the opt-out for users who
    # want the bare template even inside a known project.
    init.add_argument(
        "--no-detect",
        dest="detect",
        action="store_false",
        default=True,
        help="Skip fly.toml / wrangler.toml detection; emit the bare template.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    command = args.command or "monitor"

    # Skip the PyPI check on `init` (first-run friendliness) and when the
    # user opts out via flag. Other subcommands always pass through, so a
    # routine `paperbark monitor` invocation gets the prompt at most once
    # per ``check_interval_hours``.
    if command != "init" and getattr(args, "auto_update", None) is not False:
        _maybe_autoupdate(args)

    if command == "search":
        try:
            return _run_search(args)
        except KeyboardInterrupt:
            return 130

    if command == "monitor":
        try:
            return _run_monitor(args)
        except KeyboardInterrupt:
            return 130

    if command == "analyse":
        try:
            return _run_analyse(args)
        except KeyboardInterrupt:
            return 130

    if command == "init":
        from paperbark.init import run as run_init

        return run_init(args)

    sys.stderr.write(f"paperbark {__version__}: '{command}' is not yet implemented.\n")
    return _NOT_IMPLEMENTED_EXIT


def _maybe_autoupdate(args: argparse.Namespace) -> None:
    """Run the PyPI version check ahead of the real subcommand.

    The autoupdate config lives inside ``paperbark.toml``; if discovery or
    parsing fails we silently fall back to defaults rather than blocking the
    user's command. The actual subcommand still does its own strict load
    afterwards, so a malformed config will surface there with a typed error.
    """
    from paperbark import autoupdate
    from paperbark.config import AutoupdateConfig, ConfigError, load

    config_arg = getattr(args, "config", None)
    config_path = Path(config_arg) if config_arg else None
    try:
        cfg = load(config_path)
        autoupdate_cfg = cfg.autoupdate
    except (ConfigError, OSError):
        autoupdate_cfg = AutoupdateConfig()

    autoupdate.maybe_run(
        enabled=autoupdate_cfg.enabled,
        mode=autoupdate_cfg.mode,
        check_interval_hours=autoupdate_cfg.check_interval_hours,
        assume_yes=bool(getattr(args, "assume_yes", False)),
    )


def _load_config(args: argparse.Namespace) -> Config | int:
    """Load the TOML config based on ``args.config`` (explicit path) or discovery.

    Returns the parsed :class:`Config` on success or an exit code (``2``) when
    the file is unreadable / malformed; the caller propagates the exit code so
    the user sees the documented config-error stderr line. Pulled out of the
    per-subcommand helpers so all three (monitor/analyse/search) report the
    same way and don't drift.
    """
    from paperbark.config import ConfigError, load

    config_arg = getattr(args, "config", None)
    config_path = Path(config_arg) if config_arg else None
    try:
        return load(config_path)
    except ConfigError as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 2


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
    from paperbark.banner import print_banner
    from paperbark.dispatcher import DispatcherError, run_monitor_loop

    config = _load_config(args)
    if isinstance(config, int):
        return config

    try:
        monitor_cfg = _merge_monitor_overrides(config.monitor, args)
    except ValueError as exc:
        sys.stderr.write(f"monitor error: {exc}\n")
        return 2

    snapshot_runner = _make_snapshot_runner(config.root, run_analyse, config.probes)

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
                # ``transient=False`` on the Live region means console.print
                # calls during the run render above the ticker — so the banner
                # appears once at the top, the spinner sits below it.
                def _on_start(start: MonitorStart) -> None:
                    print_banner(start, console=ticker.console, show_quit_hint=True)

                result = run_monitor_loop(
                    config,
                    monitor=monitor_cfg,
                    stop_event=stop_event,
                    on_start=_on_start,
                    on_state=ticker.update,
                    snapshot_runner=snapshot_runner,
                )
        else:

            def _on_start_plain(start: MonitorStart) -> None:
                print_banner(start, console=None, show_quit_hint=False)

            result = run_monitor_loop(
                config,
                monitor=monitor_cfg,
                stop_event=stop_event,
                on_start=_on_start_plain,
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
    cleanup_enabled = base.cleanup_enabled
    cleanup_days = base.cleanup_days
    cleanup_mode = base.cleanup_mode

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
        from paperbark.config import RUN_ID_HELP, is_valid_run_id

        # The TOML path validates run_id against the same regex; the CLI
        # override path used to skip it, which let `--run-id ../escape`
        # silently bypass the path-safety check we apply via TOML.
        if not is_valid_run_id(run_id_arg):
            raise ValueError(f"--run-id: {RUN_ID_HELP}")
        run_id = run_id_arg

    cleanup_enabled_arg = getattr(args, "cleanup_enabled", None)
    if cleanup_enabled_arg is not None:
        cleanup_enabled = bool(cleanup_enabled_arg)

    cleanup_days_arg = getattr(args, "cleanup_days", None)
    if cleanup_days_arg is not None:
        if cleanup_days_arg < 0:
            raise ValueError("--cleanup-days must be >= 0")
        cleanup_days = cleanup_days_arg

    cleanup_mode_arg = getattr(args, "cleanup_mode", None)
    if cleanup_mode_arg is not None:
        # argparse's ``choices`` already restricts the input; the assignment
        # is just for clarity at the merge step.
        cleanup_mode = cleanup_mode_arg

    return MonitorConfig(
        interval=interval,
        iterations=iterations,
        analyse_every=analyse_every,
        run_id=run_id,
        cleanup_enabled=cleanup_enabled,
        cleanup_days=cleanup_days,
        cleanup_mode=cleanup_mode,
    )


def _make_snapshot_runner(
    root: Path,
    run_analyse: Callable[[argparse.Namespace], int],
    probes_cfg: ProbesConfig,
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
            probes=probes_cfg,
        )
        # ``run_analyse`` reports soft failures (e.g. "no app dirs with raw
        # logs") via a non-zero return without raising. Convert that to an
        # exception so the dispatcher's ``snapshot_runner`` try/except logs
        # the failure to monitor.log instead of treating it as success.
        rc = run_analyse(ns)
        if rc != 0:
            raise RuntimeError(f"paperbark analyse exited with code {rc}")

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


def _run_analyse(args: argparse.Namespace) -> int:
    """Glue between ``cli`` argparse and :func:`paperbark.analyse.run`.

    Loads the TOML config, merges CLI flag overrides into
    :class:`AnalyseConfig`, then builds the :class:`argparse.Namespace` that
    the existing analyse runner consumes. Keeping the runner's Namespace API
    intact means :func:`_make_snapshot_runner` (which constructs its own
    Namespace from the dispatcher) doesn't need to change.
    """
    from paperbark.analyse import run as run_analyse

    config = _load_config(args)
    if isinstance(config, int):
        return config

    try:
        analyse_cfg = _merge_analyse_overrides(config.analyse, args)
    except ValueError as exc:
        sys.stderr.write(f"analyse error: {exc}\n")
        return 2

    root = _resolve_root(config.root, args)
    return run_analyse(
        argparse.Namespace(
            run=analyse_cfg.run,
            root=str(root),
            app=analyse_cfg.app,
            keyword=list(analyse_cfg.keywords),
            regex=list(analyse_cfg.regexes),
            out=analyse_cfg.out or None,
            stdout=analyse_cfg.stdout,
            probes=config.probes,
        )
    )


def _run_search(args: argparse.Namespace) -> int:
    """Glue between ``cli`` argparse and :func:`paperbark.search.run`.

    Same shape as :func:`_run_analyse`: load → merge → reify a Namespace that
    matches the search runner's existing contract. Search has no
    :class:`None`-friendly default for ``--run``, so an empty string falls
    through and :func:`paperbark.search.resolve_runs` resolves it to "latest".
    """
    from paperbark.search import run as run_search

    config = _load_config(args)
    if isinstance(config, int):
        return config

    try:
        search_cfg = _merge_search_overrides(config.search, args)
    except ValueError as exc:
        sys.stderr.write(f"search error: {exc}\n")
        return 2

    root = _resolve_root(config.root, args)
    # ``paperbark.search.run`` consults ``args.case_sensitive`` exclusively,
    # so we don't carry a redundant ``ignore_case`` field on the Namespace.
    # The ``--ignore-case`` CLI flag now writes to ``case_sensitive=False``
    # via the mutex group in :func:`_build_parser`.
    return run_search(
        argparse.Namespace(
            run=search_cfg.run,
            root=str(root),
            app=search_cfg.app,
            keyword=list(search_cfg.keywords),
            regex=list(search_cfg.regexes),
            case_sensitive=search_cfg.case_sensitive,
            max=search_cfg.max,
            keep_ansi=search_cfg.keep_ansi,
        )
    )


def _resolve_root(base: Path, args: argparse.Namespace) -> Path:
    """``--root`` overrides ``[paperbark].root`` for this invocation only."""
    root_arg = getattr(args, "root", None)
    return Path(root_arg) if root_arg else base


def _merge_analyse_overrides(
    base: AnalyseConfig,
    args: argparse.Namespace,
) -> AnalyseConfig:
    """Apply ``args`` overrides to ``base``, preserving defaults for unset flags.

    ``--keyword`` / ``--regex`` use ``argparse`` ``action="append"`` with
    ``default=None`` so we can tell "user supplied none" (None → keep TOML)
    apart from "user supplied an empty list" (impossible with append, so the
    fall-through to TOML is consistent). ``--out`` stays a CLI-only override
    when supplied; an empty TOML value means "use the default
    ``<run>/analysis`` base path".
    """
    from paperbark.config import AnalyseConfig

    run_value = base.run
    app = base.app
    keywords = base.keywords
    regexes = base.regexes
    out = base.out
    stdout = base.stdout

    run_arg = getattr(args, "run", None)
    if run_arg is not None:
        run_value = run_arg

    app_arg = getattr(args, "app", None)
    if app_arg is not None:
        app = app_arg

    keyword_arg = getattr(args, "keyword", None)
    if keyword_arg is not None:
        keywords = tuple(keyword_arg)

    regex_arg = getattr(args, "regex", None)
    if regex_arg is not None:
        regexes = tuple(regex_arg)

    out_arg = getattr(args, "out", None)
    if out_arg is not None:
        out = out_arg

    stdout_arg = getattr(args, "stdout", None)
    if stdout_arg is not None:
        stdout = bool(stdout_arg)

    return AnalyseConfig(
        run=run_value,
        app=app,
        keywords=keywords,
        regexes=regexes,
        out=out,
        stdout=stdout,
    )


def _merge_search_overrides(
    base: SearchConfig,
    args: argparse.Namespace,
) -> SearchConfig:
    """Apply ``args`` overrides to ``base``, preserving defaults for unset flags.

    ``--max`` validation matches the TOML loader (>= 0; 0 = unlimited) so a
    hostile or careless ``--max -1`` fails fast rather than silently being
    treated as unlimited by ``paperbark.search``.
    """
    from paperbark.config import SearchConfig

    run_value = base.run
    app = base.app
    keywords = base.keywords
    regexes = base.regexes
    case_sensitive = base.case_sensitive
    max_matches = base.max
    keep_ansi = base.keep_ansi

    run_arg = getattr(args, "run", None)
    if run_arg is not None:
        run_value = run_arg

    app_arg = getattr(args, "app", None)
    if app_arg is not None:
        app = app_arg

    keyword_arg = getattr(args, "keyword", None)
    if keyword_arg is not None:
        keywords = tuple(keyword_arg)

    regex_arg = getattr(args, "regex", None)
    if regex_arg is not None:
        regexes = tuple(regex_arg)

    case_sensitive_arg = getattr(args, "case_sensitive", None)
    if case_sensitive_arg is not None:
        case_sensitive = bool(case_sensitive_arg)

    max_arg = getattr(args, "max", None)
    if max_arg is not None:
        if max_arg < 0:
            raise ValueError("--max must be >= 0 (0 = unlimited)")
        max_matches = max_arg

    keep_ansi_arg = getattr(args, "keep_ansi", None)
    if keep_ansi_arg is not None:
        keep_ansi = bool(keep_ansi_arg)

    return SearchConfig(
        run=run_value,
        app=app,
        keywords=keywords,
        regexes=regexes,
        case_sensitive=case_sensitive,
        max=max_matches,
        keep_ansi=keep_ansi,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
