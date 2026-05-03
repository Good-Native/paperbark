"""Search captured logs by keyword/regex across one or more apps.

Reads raw captures from a ``logs/YYYYMMDD/HHMM_<run-id>/<app>/raw/`` directory
or the zipped ``raw.zip`` produced by monitor cleanup. Prints matching lines
with a source prefix and a per-app/per-run match count summary on stderr.

Ported from ``reference/search_logs.py`` with behaviour preserved verbatim,
including the ``--ignore-case`` default-on flag (kept for documentation
symmetry with ``--case-sensitive``).
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path


def _candidate_run_dirs(root: Path) -> list[Path]:
    """Return run directories under ``logs/YYYYMMDD/HHMM_*`` sorted oldest -> newest."""
    out: list[Path] = []
    if not root.exists():
        return out
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir() or len(date_dir.name) != 8 or not date_dir.name.isdigit():
            continue
        for run in sorted(date_dir.iterdir()):
            if not run.is_dir():
                continue
            # Run-dir contract: ``HHMM_<slug>_<settings>`` — require the
            # leading ``HHMM_`` so stray sibling dirs (e.g. ``.tmp``) don't
            # poison ``--run latest`` resolution.
            name = run.name
            if len(name) < 6 or not name[:4].isdigit() or name[4] != "_":
                continue
            out.append(run)
    return out


def resolve_runs(run_arg: str | None, root: Path) -> list[Path]:
    """Resolve the ``--run`` argument to one or more run directories.

    ``None`` / ``"latest"`` -> ``[most recent run]``
    ``"all"``               -> every run
    ``"YYYYMMDD"``          -> runs under that date
    other                   -> prefix match against ``<date>/<runname>`` or run name
    """
    runs = _candidate_run_dirs(root)
    if not runs:
        return []
    if run_arg is None or run_arg in ("", "latest"):
        return [runs[-1]]
    if run_arg == "all":
        return runs
    # Normalise to forward slashes so a Windows operator passing
    # ``20260503\1430`` matches the same runs as ``20260503/1430``.
    target = run_arg.replace("\\", "/").strip("/")
    if not target:
        # ``--run "/"`` (or any value that strips to empty) would otherwise let
        # every ``rel.startswith(target)`` match — a malformed selector
        # silently behaving like ``--run all``. Fail closed: no runs matched.
        return []
    matched: list[Path] = []
    for r in runs:
        rel = r.relative_to(root).as_posix()
        # Path-prefix match (e.g. "20260503/1430" against "20260503/1430_run_a").
        # No trailing-slash constraint so partial run-name suffixes also match,
        # consistent with the docstring's "prefix match against <date>/<runname>".
        if rel == target or rel.startswith(target):
            matched.append(r)
            continue
        if r.parent.name == target or r.name == target or r.name.startswith(target):
            matched.append(r)
    return matched


def _iter_app_dirs(run_dir: Path, app_filter: list[str] | None) -> Iterator[Path]:
    for child in sorted(run_dir.iterdir()):
        if not child.is_dir():
            continue
        if app_filter and child.name not in app_filter:
            continue
        if (child / "raw").exists() or (child / "raw.zip").exists():
            yield child


def iter_lines(app_dir: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(source_label, line)`` for every captured raw log line in an app dir."""
    raw_dir = app_dir / "raw"
    if raw_dir.exists():
        for log in sorted(raw_dir.glob("*.log")):
            with log.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    yield (log.name, line.rstrip("\n"))
    raw_zip = app_dir / "raw.zip"
    if raw_zip.exists():
        # Corrupt or truncated archives (e.g. an upstream cleanup killed mid-write)
        # surface as BadZipFile/OSError on open. Skip with a warning so a single
        # bad zip doesn't abort the rest of a `--run all` search.
        try:
            zf_open = zipfile.ZipFile(raw_zip)
        except (zipfile.BadZipFile, OSError) as exc:
            print(f"# skipping unreadable {raw_zip}: {exc}", file=sys.stderr)
            return
        with zf_open as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                if info.is_dir() or not info.filename.endswith(".log"):
                    continue
                # Per-member CRC validation can fail mid-read on a corrupted entry
                # even when the central directory opened fine. Skip the entry and
                # carry on so one bad member doesn't abort the rest of the archive.
                try:
                    with zf.open(info) as f:
                        for raw_line in f:
                            yield (
                                Path(info.filename).name,
                                raw_line.decode("utf-8", errors="ignore").rstrip("\n"),
                            )
                except (zipfile.BadZipFile, OSError) as exc:
                    print(
                        f"# skipping unreadable member {raw_zip}:{info.filename}: {exc}",
                        file=sys.stderr,
                    )
                    continue


def search_runs(
    runs: list[Path],
    pattern: re.Pattern[str],
    app_filter: list[str] | None,
    max_matches: int,
    root: Path,
) -> int:
    """Print matching lines and per-run summaries; return the total match count."""
    total = 0
    stop = False
    for run in runs:
        per_app: dict[str, int] = {}
        # POSIX-form so the prefix renders identically across platforms.
        rel_run = run.relative_to(root).as_posix()
        for app_dir in _iter_app_dirs(run, app_filter):
            count = 0
            for source, line in iter_lines(app_dir):
                if pattern.search(line):
                    count += 1
                    total += 1
                    print(f"[{rel_run}][{app_dir.name}][{source}] {line}")
                    if max_matches and total >= max_matches:
                        stop = True
                        break
            per_app[app_dir.name] = count
            if stop:
                break
        for app, n in per_app.items():
            print(f"# {rel_run} {app}: {n} match(es)", file=sys.stderr)
        if stop:
            print("# match cap reached", file=sys.stderr)
            break
    print(f"# total matches: {total}", file=sys.stderr)
    return total


def run(args: argparse.Namespace) -> int:
    """Entry point invoked from ``paperbark.cli.main`` for the ``search`` subcommand."""
    if not args.keyword and not args.regex:
        print("Provide at least one --keyword or --regex.", file=sys.stderr)
        return 2
    if args.max < 0:
        print("--max must be >= 0 (0 = unlimited).", file=sys.stderr)
        return 2

    parts = [re.escape(k) for k in args.keyword] + list(args.regex)
    flags = 0 if args.case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile("|".join(f"(?:{p})" for p in parts), flags)
    except re.error as exc:
        print(f"Invalid regex: {exc}", file=sys.stderr)
        return 2

    root = Path(args.root)
    runs = resolve_runs(args.run, root)
    if not runs:
        print(f"No runs matched under {root} (run={args.run!r})", file=sys.stderr)
        return 1

    apps = [a.strip() for a in args.app.split(",") if a.strip()] or None
    search_runs(runs, pattern, apps, args.max, root)
    return 0
