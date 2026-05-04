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


# `paperbark monitor` cadence and identity. CLI flags override these.
[monitor]
# Seconds between iterations. Accepts plain seconds or "30s"/"5m"/"1h".
interval = 3
# Total iterations to run. 0 = forever.
iterations = 1440
# Snapshot analyse cadence. 0 disables snapshots.
analyse_every = "5m"
# Run identifier. Empty = auto-generated <adjective>-<colour> slug.
# Letters, numbers, '.', '_', '-' only; may not start with '.' or '-'.
run_id = ""


# `paperbark analyse` defaults. CLI flags override these.
[analyse]
# Run selector: "latest", "all", "<date>", or "<date>/<runname>".
run = "latest"
# Comma-separated app filter. Empty = every app under the run.
app = ""
# Ad-hoc keyword/regex matchers added on top of the default probe set.
keywords = []
regexes = []
# Override output base path (writes <out>.json + <out>.md). Empty =
# write <run>/analysis.{json,md} as usual.
out = ""
# Also print rendered markdown to stdout in addition to writing files.
stdout = false


# `paperbark search` defaults. CLI flags override these.
[search]
# Run selector grammar matches [analyse].run.
run = "latest"
app = ""
# Repeatable keyword/regex matchers; supply at least one at run time.
keywords = []
regexes = []
# Case-sensitive matching (default off). The CLI --case-sensitive flag
# overrides; --ignore-case is documentation-only.
case_sensitive = false
# Stop after N total matches (0 = unlimited).
max = 0


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
