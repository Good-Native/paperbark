"""Tests for ``paperbark.search`` and the ``paperbark search`` CLI dispatch."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from paperbark.cli import main
from paperbark.search import iter_lines, resolve_runs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def fake_logs(tmp_path: Path) -> Path:
    """Build a fake logs/ tree spanning two dates, two apps, and both raw layouts."""
    root = tmp_path / "logs"
    _write(
        root / "20260503" / "1430_run_a" / "app1" / "raw" / "app.1.log",
        "panic: db down\nINFO ok\n",
    )
    raw_zip = root / "20260503" / "1500_run_b" / "app2" / "raw.zip"
    raw_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw_zip, "w") as zf:
        zf.writestr("app.1.log", "PANIC: ouch\nWARN slow\n")
    _write(
        root / "20260504" / "0900_run_c" / "app1" / "raw" / "app.1.log",
        "[error] thing failed\nINFO ok\n",
    )
    return root


def test_resolve_runs_latest(fake_logs: Path) -> None:
    runs = resolve_runs(None, fake_logs)
    assert [r.name for r in runs] == ["0900_run_c"]


def test_resolve_runs_all(fake_logs: Path) -> None:
    runs = resolve_runs("all", fake_logs)
    assert [r.name for r in runs] == ["1430_run_a", "1500_run_b", "0900_run_c"]


def test_resolve_runs_date_prefix(fake_logs: Path) -> None:
    runs = resolve_runs("20260503", fake_logs)
    assert {r.name for r in runs} == {"1430_run_a", "1500_run_b"}


def test_resolve_runs_run_name_prefix(fake_logs: Path) -> None:
    runs = resolve_runs("1430", fake_logs)
    assert [r.name for r in runs] == ["1430_run_a"]


def test_resolve_runs_no_match_exits_1(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["search", "--root", str(fake_logs), "--run", "9999", "--keyword", "x"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "No runs matched" in err


def test_iter_lines_raw_dir(fake_logs: Path) -> None:
    app = fake_logs / "20260503" / "1430_run_a" / "app1"
    lines = list(iter_lines(app))
    assert ("app.1.log", "panic: db down") in lines
    assert ("app.1.log", "INFO ok") in lines


def test_iter_lines_raw_zip(fake_logs: Path) -> None:
    app = fake_logs / "20260503" / "1500_run_b" / "app2"
    lines = list(iter_lines(app))
    assert ("app.1.log", "PANIC: ouch") in lines
    assert ("app.1.log", "WARN slow") in lines


def test_keyword_escapes_regex_metachars(
    fake_logs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "0900_run_c",
            "--keyword",
            "[error]",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[error] thing failed" in out


def test_regex_pattern_interpreted(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "0900_run_c",
            "--regex",
            r"^\[error\]",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[error] thing failed" in out


def test_case_insensitive_default(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "1500_run_b",
            "--keyword",
            "panic",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "PANIC: ouch" in out


def test_case_sensitive_flag_excludes_uppercase(
    fake_logs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "1500_run_b",
            "--case-sensitive",
            "--keyword",
            "panic",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "PANIC: ouch" not in captured.out
    assert "# total matches: 0" in captured.err


def test_max_cap_stops_search(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "all",
            "--keyword",
            "ok",
            "--max",
            "1",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.count("INFO ok") == 1
    assert "# match cap reached" in captured.err


def test_app_filter_excludes_other_apps(
    fake_logs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "20260503",
            "--app",
            "app1",
            "--keyword",
            "panic",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "panic: db down" in out
    assert "PANIC: ouch" not in out


def test_no_pattern_args_exits_2(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["search", "--root", str(fake_logs), "--run", "all"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "at least one --keyword or --regex" in err


def test_invalid_regex_exits_2(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "all",
            "--regex",
            "[",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "Invalid regex" in err


def test_negative_max_exits_2(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "all",
            "--keyword",
            "x",
            "--max",
            "-1",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "--max must be >= 0" in err


def test_empty_root_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(
        [
            "search",
            "--root",
            str(empty),
            "--run",
            "all",
            "--keyword",
            "x",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "No runs matched" in err


def test_match_output_format(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "1430_run_a",
            "--keyword",
            "panic",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "[app1][app.1.log] panic: db down" in out


def test_summary_to_stderr(fake_logs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            "search",
            "--root",
            str(fake_logs),
            "--run",
            "1430_run_a",
            "--keyword",
            "panic",
        ]
    )
    err = capsys.readouterr().err
    assert rc == 0
    assert "# total matches: 1" in err
    assert "app1: 1 match(es)" in err
