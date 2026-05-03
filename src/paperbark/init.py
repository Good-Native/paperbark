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
# paperbark configuration.
#
# Discovered in this order:
#   1. ./paperbark.toml
#   2. ~/.config/paperbark/config.toml
#
# CLI flags override these values at runtime. Every flag has a TOML key here.

[paperbark]
# Output root for captured runs. Each capture lands in
#   logs/YYYYMMDD/HHMM_<slug>_<settings>/
root = "logs"


# Probe toggles. Set any to false to disable that probe entirely.
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

# Ad-hoc keyword and regex matchers run alongside the built-in probes.
# Add literals to `keywords` (escaped at match time) or patterns to `regexes`.
keywords = []
regexes = []


# Per-probe pattern overrides. Each key is a probe name; each value is an
# array of {label, pattern} tables. Use these to extend or replace the
# built-in regex sets without forking — handy for non-Fly platforms whose log
# vocabulary differs from the defaults.
#
# [probes.patterns]
# autoscaler = [
#     { label = "reconciling", pattern = "reconciling app" },
# ]
# database = [
#     { label = "pg-deadlock", pattern = "deadlock detected" },
# ]


# Sources to capture. Each [[sources]] entry needs a unique `name` and a
# `type` (currently: "flyctl"). Type-specific keys go on the same table.
#
# [[sources]]
# name = "main"
# type = "flyctl"
# app = "your-fly-app"
#
# [[sources]]
# name = "worker"
# type = "flyctl"
# app = "your-fly-worker"
"""


class InitError(RuntimeError):
    """Raised when the starter file cannot be written for a documented reason."""


def write_starter(path: Path, *, force: bool = False) -> None:
    """Write the starter TOML to ``path``.

    Refuses to overwrite an existing file unless ``force=True``. Creates parent
    directories as needed. Raises :class:`InitError` for the documented refusal
    cases so the CLI layer can map them to exit codes.
    """
    if path.exists() and not force:
        raise InitError(
            f"{path} already exists. Re-run with --force to overwrite.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_TOML, encoding="utf-8")


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
