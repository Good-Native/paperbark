"""``paperbark init`` — write a starter ``paperbark.toml`` to the working directory.

The emitted template documents every key the config layer recognises and
round-trips cleanly through :func:`paperbark.config.from_dict`. Every value is
either at its built-in default or shown as a commented-out example, so a
freshly-emitted file parses to :meth:`paperbark.config.Config.defaults`.
Tests pin this contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_OUTPUT = "paperbark.toml"

STARTER_TOML = """\
# paperbark configuration. Full reference: docs/CONFIG.md.
# CLI flags override every key. At least one [[sources]] entry is required.

[paperbark]
root = "logs"

[monitor]
interval = 3
iterations = 1440
analyse_every = "5m"
run_id = ""
cleanup_enabled = true
cleanup_days = 1
cleanup_mode = "zip"

[probes]
severity = true
panics = true
http = true
latency = true
heartbeat = true
process_health = true
autoscaler = true
database = true
sentry = true
keywords = []
regexes = []

# [probes.patterns]
# autoscaler = [{ label = "reconciling", pattern = "reconciling app" }]

[analyse]
run = "latest"
app = ""
keywords = []
regexes = []
out = ""
stdout = false

[search]
run = "latest"
app = ""
keywords = []
regexes = []
case_sensitive = false
max = 0
keep_ansi = false

# [[sources]]
# name = "main"
# type = "flyctl"
# app = "your-fly-app"
"""


class InitError(RuntimeError):
    """Raised when the starter file cannot be written for a documented reason."""


def write_starter(path: Path, *, force: bool = False) -> None:
    """Write the starter TOML to ``path``.

    Refuses to overwrite an existing file unless ``force=True``. Creates parent
    directories as needed. Raises :class:`InitError` for the documented refusal
    cases so the CLI layer can map them to exit codes.

    The non-force path uses exclusive-create (``"x"``) so the existence check
    and the write are atomic at the OS level — a TOCTOU race where another
    process creates the file between checks would otherwise let us silently
    overwrite without ``--force``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if force:
        path.write_text(STARTER_TOML, encoding="utf-8")
        return
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(STARTER_TOML)
    except FileExistsError as exc:
        raise InitError(
            f"{path} already exists. Re-run with --force to overwrite.",
        ) from exc


def run(args: argparse.Namespace) -> int:
    """Entry point invoked from ``paperbark.cli.main`` for the ``init`` subcommand."""
    target = Path(args.path)
    try:
        write_starter(target, force=args.force)
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not write {target}: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote starter config to {target}", file=sys.stderr)
    return 0
