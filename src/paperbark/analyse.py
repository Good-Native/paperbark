"""Replay a captured run through the probe layer and emit reports.

Picks one or more existing run directories (per ``--run latest|all|<id>``),
streams every captured raw line through :func:`paperbark.probes.parse_line`,
feeds the resulting :class:`CanonicalRecord` into the default probe set,
and writes ``analysis.json`` plus ``analysis.md`` at the run root.

Ported from ``reference/analyse_logs.py``. Reuses
:func:`paperbark.search.resolve_runs` and :func:`paperbark.search.iter_lines`
so run-discovery and capture-reading semantics stay in one place.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from paperbark.config import ProbesConfig
from paperbark.probes import default_probes, parse_line
from paperbark.probes._record import strip_ansi
from paperbark.search import iter_lines, resolve_runs

# Capture-time cursor filtering already removes near-all duplicates; this
# bounded sliding-window LRU is the secondary safety net for sources whose
# capture windows overlap (e.g. flyctl's ``--no-tail``). Sized to absorb a
# few iterations' worth of lines on a busy app without unbounded growth.
_DEDUPE_WINDOW = 50_000


def _select_app_dirs(run: Path, app_filter: list[str] | None) -> list[Path]:
    """Return app dirs under ``run`` that hold captured logs."""
    out: list[Path] = []
    for child in sorted(run.iterdir()):
        if not child.is_dir():
            continue
        if app_filter and child.name not in app_filter:
            continue
        if (child / "raw").exists() or (child / "raw.zip").exists():
            out.append(child)
    return out


def _is_json_record(line: str) -> bool:
    """Return True when ``line`` carries a JSON object payload.

    Mirrors the reference's ``parsed_records`` test: a line counts as a
    parsed record when there is a balanced JSON object somewhere in it.
    """
    idx = line.find("{")
    if idx == -1:
        return False
    try:
        rec = json.loads(line[idx:])
    except json.JSONDecodeError:
        return False
    return isinstance(rec, dict)


def _analyse_app(
    app_dir: Path,
    extra_keywords: list[str],
    extra_regexes: list[str],
    probes_cfg: ProbesConfig,
) -> dict[str, Any]:
    """Run the configured probe set against ``app_dir`` and roll up the report."""
    probes = default_probes(extra_keywords, extra_regexes, config=probes_cfg)
    total_lines = 0
    unique_lines = 0
    parsed_records = 0
    first_ts = ""
    last_ts = ""
    seen: set[str] = set()
    seen_order: deque[str] = deque()

    for _source, raw_line in iter_lines(app_dir):
        total_lines += 1
        cleaned = strip_ansi(raw_line).rstrip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        seen_order.append(cleaned)
        if len(seen_order) > _DEDUPE_WINDOW:
            seen.discard(seen_order.popleft())
        unique_lines += 1
        if _is_json_record(cleaned):
            parsed_records += 1
        record = parse_line(cleaned)
        ts = record.timestamp
        if ts:
            if not first_ts or ts < first_ts:
                first_ts = ts
            if not last_ts or ts > last_ts:
                last_ts = ts
        for probe in probes:
            try:
                probe.feed(record)
            except Exception as exc:
                # One malformed line must not abort an entire-run analysis.
                sys.stderr.write(f"warn: probe {probe.name} on {app_dir.name}: {exc}\n")

    return {
        "app": app_dir.name,
        "total_lines": total_lines,
        "unique_lines": unique_lines,
        "parsed_records": parsed_records,
        "first_seen": first_ts,
        "last_seen": last_ts,
        "probes": [probe.report() for probe in probes],
    }


def _fmt_window(finding: dict[str, Any]) -> str:
    first = finding.get("first_seen") or "-"
    last = finding.get("last_seen") or "-"
    peak = finding.get("peak") or "-"
    peak_count = finding.get("peak_count") or 0
    return f"first={first} last={last} peak={peak}({peak_count})"


def _render_md(report: dict[str, Any]) -> str:
    out: list[str] = []
    out.append(f"# Log analysis — {report['run']}")
    out.append("")
    out.append(f"Generated: {report['generated_at']}")
    if report.get("extra_keywords") or report.get("extra_regexes"):
        out.append(
            f"Ad-hoc terms: keywords={report.get('extra_keywords')}"
            f" regexes={report.get('extra_regexes')}"
        )
    out.append("")

    for app in report["apps"]:
        out.append(f"## {app['app']}")
        unique = app.get("unique_lines", app["total_lines"])
        out.append(
            f"- lines: {app['total_lines']:,} captured / {unique:,} unique"
            f" (parsed JSON: {app['parsed_records']:,})"
            f" | window: {app.get('first_seen') or '-'} → {app.get('last_seen') or '-'}"
        )
        for probe in app["probes"]:
            out.append("")
            out.append(f"### {probe['name']}")
            if probe.get("note"):
                out.append(f"_{probe['note']}_")
            if probe["name"] == "Latency":
                if probe.get("samples"):
                    out.append(
                        f"- samples: {probe['samples']} | "
                        f"p50={probe['p50_ms']:.0f}ms p95={probe['p95_ms']:.0f}ms "
                        f"p99={probe['p99_ms']:.0f}ms max={probe['max_ms']:.0f}ms"
                    )
                    if probe.get("slowest"):
                        out.append("")
                        out.append("Slowest:")
                        for slow in probe["slowest"][:5]:
                            out.append(
                                f"  - {slow['duration_ms']:.0f}ms"
                                f" @ {slow['timestamp']}: {slow['line']}"
                            )
                continue
            if probe["name"] == "Heartbeat":
                out.append(
                    f"- median info/min: {probe.get('median_info_per_minute')} | "
                    f"window: {probe.get('first_minute')} → {probe.get('last_minute')}"
                )
                gaps = probe.get("gap_minutes") or []
                if gaps:
                    out.append(f"- {len(gaps)} zero-info minute(s):")
                    for gap in gaps[:10]:
                        out.append(f"  - {gap['minute']}")
                continue
            findings = probe.get("findings") or []
            if not findings:
                out.append("- (no matches)")
                continue
            for finding in findings:
                out.append(f"- {finding['label']}: {finding['count']} | {_fmt_window(finding)}")
                for sample in (finding.get("samples") or [])[:1]:
                    out.append(f"    sample: {sample}")
        out.append("")
    return "\n".join(out)


def _write_run_report(
    run_dir: Path,
    root: Path,
    apps: list[Path],
    args: argparse.Namespace,
) -> tuple[Path, Path, str]:
    """Build, render, and write the report for one run."""
    keywords = list(getattr(args, "keyword", []) or [])
    regexes = list(getattr(args, "regex", []) or [])
    probes_cfg = getattr(args, "probes", None) or ProbesConfig()
    app_reports = [_analyse_app(d, keywords, regexes, probes_cfg) for d in apps]
    report: dict[str, Any] = {
        "run": run_dir.relative_to(root).as_posix(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "extra_keywords": keywords,
        "extra_regexes": regexes,
        "apps": app_reports,
    }
    out_arg = getattr(args, "out", None)
    base = Path(out_arg) if out_arg else run_dir / "analysis"
    base.parent.mkdir(parents=True, exist_ok=True)
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    md = _render_md(report)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(md + "\n", encoding="utf-8")
    return json_path, md_path, md


def run(args: argparse.Namespace) -> int:
    """Entry point invoked from ``paperbark.cli.main`` for ``analyse``."""
    root = Path(getattr(args, "root", "logs") or "logs")
    runs = resolve_runs(args.run, root)
    if not runs:
        sys.stderr.write(f"No runs matched under {root} (run={args.run!r})\n")
        return 1
    out_arg = getattr(args, "out", None)
    if len(runs) > 1 and out_arg:
        # ``--out`` shares a single base path; multiple runs would otherwise
        # silently overwrite each other. Without ``--out`` each run writes
        # to its own ``<run>/analysis.{md,json}`` and multi-run is fine.
        sys.stderr.write(
            f"Resolved {len(runs)} runs; --out requires a single run because"
            " combined output isn't implemented.\n"
        )
        return 2

    app_filter_raw = getattr(args, "app", "") or ""
    app_filter = [a.strip() for a in app_filter_raw.split(",") if a.strip()] or None

    rc = 0
    for selected_run in runs:
        app_dirs = _select_app_dirs(selected_run, app_filter)
        if not app_dirs:
            sys.stderr.write(f"No app dirs with raw logs under {selected_run}\n")
            rc = rc or 1
            continue
        json_path, md_path, md = _write_run_report(selected_run, root, app_dirs, args)
        sys.stdout.write(f"wrote {md_path}\n")
        sys.stdout.write(f"wrote {json_path}\n")
        if getattr(args, "stdout", False):
            sys.stdout.write(md + "\n")
    return rc
