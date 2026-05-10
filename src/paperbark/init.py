"""``paperbark init`` — write a starter ``paperbark.toml`` to the working directory.

The base template documents every key the config layer recognises and
round-trips cleanly through :func:`paperbark.config.from_dict`. Every
default value is either at its built-in default or shown as a
commented-out example, so a freshly-emitted file with no detected
sources parses to :meth:`paperbark.config.Config.defaults`. Tests pin
this contract.

When the working directory contains a ``fly.toml`` or
``wrangler.{toml,jsonc}``, the trailing commented-out ``[[sources]]``
example is replaced with real, ready-to-run entries — see
:mod:`paperbark.detect` for the rules. Pass ``--no-detect`` to skip
detection and emit the bare template unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from paperbark.detect import DetectedSource, detect


def _toml_basic_string(value: str) -> str:
    """Quote ``value`` as a TOML basic string.

    JSON string encoding is a strict subset of TOML basic-string syntax
    (both escape ``"``, ``\\``, ``\\b``, ``\\f``, ``\\n``, ``\\r``,
    ``\\t``, and ``\\uXXXX`` identically), so :func:`json.dumps` is a
    safe one-liner here. Fly app names and Cloudflare worker names are
    in practice restricted to lowercase alphanumerics and hyphens, but
    ``account_id`` and any future field reaching this helper might not
    be — emitting raw ``f'"{value}"'`` would corrupt the file on a
    stray quote or newline.
    """
    return json.dumps(value)


DEFAULT_OUTPUT = "paperbark.toml"

_BASE_TEMPLATE = """\
# paperbark configuration. Full reference: docs/CONFIG.md.
# CLI flags override every key. At least one [[sources]] entry is required.

[paperbark]
root = "logs"

[monitor]
interval = 3            # Accepts plain seconds or "30s"/"5m"/"1h"
iterations = 1440
analyse_every = "5m"    # Accepts plain seconds or "30s"/"5m"/"1h"
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
run = "latest"          # "latest", "all", "<date>", or "<date>/<runname>"
app = ""                # Comma-separated app filter; empty = all apps
keywords = []           # Extra literal terms folded into probes
regexes = []            # Extra regex terms folded into probes
out = ""                # Override output base path; empty = analysis.{json,md}
stdout = false          # Also echo rendered markdown to stdout

[search]
run = "latest"          # Same selector grammar as [analyse].run
app = ""                # Comma-separated app filter; empty = all apps
keywords = []           # Literal terms; at least one keyword or regex required
regexes = []            # Regex terms; at least one keyword or regex required
case_sensitive = false  # Default off; matches case-insensitively
max = 0                 # Stop after N matches; 0 = unlimited
keep_ansi = false       # Strip ANSI by default so pipes stay readable

"""

_SOURCES_PLACEHOLDER = """\
# [[sources]]
# name = "main"
# type = "flyctl"
# app = "your-fly-app"
"""

# The base template (commented-out source example appended) is what
# tests pin via ``STARTER_TOML``. Both halves are also exposed
# separately so :func:`render_starter` can swap the placeholder for
# detected sources without re-stringifying the rest.
STARTER_TOML = _BASE_TEMPLATE + _SOURCES_PLACEHOLDER


def _render_source(detected: DetectedSource) -> str:
    """Emit one ``[[sources]]`` block for a detected manifest entry.

    Round-trips through :func:`paperbark.config.from_dict` — that's
    enforced by ``tests/test_init.py``, not by this function.
    """
    lines = [
        "[[sources]]",
        f"name = {_toml_basic_string(detected.name)}",
        f"type = {_toml_basic_string(detected.type)}",
    ]
    if detected.type == "flyctl":
        assert detected.app is not None, "flyctl detection without app"
        lines.append(f"app = {_toml_basic_string(detected.app)}")
    elif detected.type == "wrangler":
        assert detected.worker is not None, "wrangler detection without worker"
        lines.append(f"worker = {_toml_basic_string(detected.worker)}")
        if detected.account_id:
            lines.append(f"account_id = {_toml_basic_string(detected.account_id)}")
    return "\n".join(lines) + "\n"


def render_starter(detected: list[DetectedSource]) -> str:
    """Build the file contents for the starter ``paperbark.toml``.

    Empty ``detected`` returns the bare ``STARTER_TOML`` byte-for-byte;
    a non-empty list replaces the trailing commented placeholder with
    one real ``[[sources]]`` block per detected entry, separated by a
    blank line for readability.
    """
    if not detected:
        return STARTER_TOML
    blocks = [_render_source(entry) for entry in detected]
    return _BASE_TEMPLATE + "\n".join(blocks)


class InitError(RuntimeError):
    """Raised when the starter file cannot be written for a documented reason."""


def write_starter(
    path: Path,
    *,
    force: bool = False,
    detected: list[DetectedSource] | None = None,
) -> None:
    """Write the starter TOML to ``path``.

    Refuses to overwrite an existing file unless ``force=True``. Creates parent
    directories as needed. Raises :class:`InitError` for the documented refusal
    cases so the CLI layer can map them to exit codes.

    The non-force path uses exclusive-create (``"x"``) so the existence check
    and the write are atomic at the OS level — a TOCTOU race where another
    process creates the file between checks would otherwise let us silently
    overwrite without ``--force``.

    ``detected=None`` means "emit the bare template" — preserves the original
    contract for callers that don't want detection (the CLI passes the
    detection result explicitly).
    """
    contents = render_starter(detected or [])
    path.parent.mkdir(parents=True, exist_ok=True)
    if force:
        path.write_text(contents, encoding="utf-8")
        return
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(contents)
    except FileExistsError as exc:
        raise InitError(
            f"{path} already exists. Re-run with --force to overwrite.",
        ) from exc


def _format_detection_summary(detected: list[DetectedSource]) -> str:
    parts: list[str] = []
    for entry in detected:
        if entry.type == "flyctl":
            parts.append(f"flyctl (app={entry.app})")
        elif entry.type == "wrangler":
            parts.append(f"wrangler (worker={entry.worker})")
        else:
            parts.append(entry.type)
    return ", ".join(parts)


def run(args: argparse.Namespace) -> int:
    """Entry point invoked from ``paperbark.cli.main`` for the ``init`` subcommand."""
    target = Path(args.path)
    # Detection scans the current working directory, not the output
    # path's parent — the user's CWD is the project they're configuring.
    # ``--path`` only chooses where to write the resulting config.
    detected: list[DetectedSource] = detect(Path.cwd()) if getattr(args, "detect", True) else []
    try:
        write_starter(target, force=args.force, detected=detected)
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not write {target}: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote starter config to {target}", file=sys.stderr)
    if detected:
        print(
            f"Detected source(s): {_format_detection_summary(detected)}",
            file=sys.stderr,
        )
    return 0
