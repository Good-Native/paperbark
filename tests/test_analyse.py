"""Tests for ``paperbark.analyse`` and the ``paperbark analyse`` CLI dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from paperbark.analyse import run as run_analyse
from paperbark.cli import main


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_run(
    root: Path,
    *,
    date: str = "20260503",
    run_name: str = "1430_demo",
    app: str = "demo-app",
    lines: list[str] | None = None,
) -> Path:
    """Return the run directory after seeding ``raw/sample.log``."""
    if lines is None:
        lines = [
            '{"time":"2026-05-03T14:30:01Z","level":"info","msg":"hello"}',
            '{"time":"2026-05-03T14:30:02Z","level":"info","msg":"world"}',
            '{"time":"2026-05-03T14:30:03Z","level":"error","msg":"bang"}',
            "panic: db down",
            '{"time":"2026-05-03T14:30:05Z","level":"info","msg":"req"'
            ',"status":500,"duration_ms":120}',
            '{"time":"2026-05-03T14:30:06Z","level":"info","msg":"req"'
            ',"status":200,"duration_ms":15}',
        ]
    run_dir = root / date / run_name
    _write(run_dir / app / "raw" / "sample.log", "\n".join(lines) + "\n")
    return run_dir


def _ns(**overrides: object) -> argparse.Namespace:
    """Build the Namespace the analyse runner expects.

    Each call gets fresh ``keyword`` / ``regex`` lists so a test that
    appends to them cannot leak state into the next test.
    """
    defaults: dict[str, object] = {
        "run": "latest",
        "root": "logs",
        "app": "",
        "keyword": [],
        "regex": [],
        "out": None,
        "stdout": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_analyse_writes_json_and_md_at_run_root(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    rc = run_analyse(_ns(run="latest", root=str(root)))

    assert rc == 0
    json_path = run_dir / "analysis.json"
    md_path = run_dir / "analysis.md"
    assert json_path.exists()
    assert md_path.exists()


def test_analyse_json_top_level_shape(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    run_analyse(_ns(run="latest", root=str(root)))

    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "run",
        "generated_at",
        "extra_keywords",
        "extra_regexes",
        "apps",
    }
    assert payload["run"] == "20260503/1430_demo"
    assert payload["extra_keywords"] == []
    assert payload["extra_regexes"] == []
    assert isinstance(payload["apps"], list) and len(payload["apps"]) == 1


def test_analyse_per_app_probes_listed(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    run_analyse(_ns(run="latest", root=str(root)))

    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    app = payload["apps"][0]
    assert app["app"] == "demo-app"
    probe_names = [p["name"] for p in app["probes"]]
    # Default probe set in reporting order; ad-hoc bucket only appears
    # when the user supplies --keyword / --regex.
    assert probe_names[:5] == ["Severity", "Panics & fatals", "HTTP status", "Latency", "Heartbeat"]
    assert "Ad-hoc keywords" not in probe_names


def test_analyse_severity_finding_shape(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    run_analyse(_ns(run="latest", root=str(root)))

    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    severity = next(p for p in payload["apps"][0]["probes"] if p["name"] == "Severity")
    info = next(f for f in severity["findings"] if f["label"] == "info")
    expected_keys = {
        "label",
        "count",
        "first_seen",
        "last_seen",
        "peak",
        "peak_count",
        "samples",
    }
    assert set(info) >= expected_keys
    assert info["count"] == 4  # four info-level lines in the fixture
    assert info["first_seen"].startswith("2026-05-03T14:30:01")


def test_analyse_keyword_adds_adhoc_bucket(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    rc = run_analyse(_ns(run="latest", root=str(root), keyword=["world"]))

    assert rc == 0
    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    adhoc = next(p for p in payload["apps"][0]["probes"] if p["name"] == "Ad-hoc keywords")
    assert any(f["label"] == "keyword:world" and f["count"] == 1 for f in adhoc["findings"])


def test_analyse_out_redirects_paths(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    _build_run(root)
    base = tmp_path / "out" / "report"

    rc = run_analyse(_ns(run="latest", root=str(root), out=str(base)))

    assert rc == 0
    assert (tmp_path / "out" / "report.json").exists()
    assert (tmp_path / "out" / "report.md").exists()


def test_analyse_run_all_writes_each_run(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_a = _build_run(root, date="20260503", run_name="1430_first")
    run_b = _build_run(root, date="20260503", run_name="1500_second")

    rc = run_analyse(_ns(run="all", root=str(root)))

    assert rc == 0
    assert (run_a / "analysis.json").exists()
    assert (run_b / "analysis.json").exists()


def test_analyse_run_all_with_out_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "logs"
    _build_run(root, run_name="1430_first")
    _build_run(root, run_name="1500_second")

    rc = run_analyse(_ns(run="all", root=str(root), out=str(tmp_path / "report")))

    assert rc == 2
    captured = capsys.readouterr()
    assert "--out requires a single run" in captured.err


def test_analyse_no_runs_matched_returns_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "logs"
    root.mkdir()

    rc = run_analyse(_ns(run="latest", root=str(root)))

    assert rc == 1
    captured = capsys.readouterr()
    assert "No runs matched" in captured.err


def test_analyse_run_with_no_app_dirs_returns_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "logs"
    # Valid run name but no app subdirectories with raw/ or raw.zip.
    (root / "20260503" / "1430_empty").mkdir(parents=True)

    rc = run_analyse(_ns(run="latest", root=str(root)))

    assert rc == 1
    captured = capsys.readouterr()
    assert "No app dirs with raw logs" in captured.err


def test_analyse_dedup_collapses_duplicate_lines(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    line = '{"time":"2026-05-03T14:30:01Z","level":"info","msg":"dup"}'
    run_dir = _build_run(root, lines=[line, line, line])

    run_analyse(_ns(run="latest", root=str(root)))

    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    app = payload["apps"][0]
    assert app["total_lines"] == 3
    assert app["unique_lines"] == 1


def test_analyse_app_filter(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = root / "20260503" / "1430_two-apps"
    _write(
        run_dir / "alpha" / "raw" / "x.log",
        '{"time":"2026-05-03T14:30:01Z","level":"info","msg":"a"}\n',
    )
    _write(
        run_dir / "beta" / "raw" / "x.log",
        '{"time":"2026-05-03T14:30:01Z","level":"info","msg":"b"}\n',
    )

    run_analyse(_ns(run="latest", root=str(root), app="beta"))

    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    assert [a["app"] for a in payload["apps"]] == ["beta"]


def test_analyse_stdout_flag_emits_markdown(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "logs"
    _build_run(root)

    run_analyse(_ns(run="latest", root=str(root), stdout=True))

    captured = capsys.readouterr()
    assert "# Log analysis" in captured.out


def test_cli_analyse_dispatch_writes_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    rc = main(["analyse", "--run", "latest", "--root", str(root)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    assert (run_dir / "analysis.json").exists()
    assert (run_dir / "analysis.md").exists()


def test_cli_analyse_repeatable_keyword_flags(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    rc = main(
        [
            "analyse",
            "--run",
            "latest",
            "--root",
            str(root),
            "--keyword",
            "world",
            "--keyword",
            "bang",
        ]
    )

    assert rc == 0
    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    # ``extra_keywords`` is the substantive flag-order assertion: argparse
    # ``action="append"`` must preserve the ``--keyword`` order verbatim.
    assert payload["extra_keywords"] == ["world", "bang"]
    adhoc = next(p for p in payload["apps"][0]["probes"] if p["name"] == "Ad-hoc keywords")
    # Findings are sorted by count desc with stable insertion-order tiebreak;
    # both keywords match exactly once in the fixture, so flag order wins.
    labels = [f["label"] for f in adhoc["findings"]]
    assert labels == ["keyword:world", "keyword:bang"]


def test_cli_analyse_repeatable_regex_flags(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)

    rc = main(
        [
            "analyse",
            "--run",
            "latest",
            "--root",
            str(root),
            "--regex",
            r"db down",
            "--regex",
            r"duration_ms",
        ]
    )

    assert rc == 0
    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    # As above: ``extra_regexes`` proves the argparse-preserved flag order.
    assert payload["extra_regexes"] == ["db down", "duration_ms"]
    adhoc = next(p for p in payload["apps"][0]["probes"] if p["name"] == "Ad-hoc keywords")
    # Findings sort by count desc: ``duration_ms`` matches twice in the
    # fixture (two HTTP records), ``db down`` once, so the count-sorted
    # order inverts the flag order. This pins probe behaviour explicitly.
    labels = [f["label"] for f in adhoc["findings"]]
    assert labels == ["regex:duration_ms", "regex:db down"]
