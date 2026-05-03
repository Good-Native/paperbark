"""Tests for ``paperbark.search`` and the ``paperbark search`` CLI dispatch."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

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


def test_resolve_runs_excludes_non_hhmm_siblings(tmp_path: Path) -> None:
    """Sibling dirs that don't match the ``HHMM_*`` contract are skipped, so
    ``latest`` cannot resolve to a stray ``.tmp`` / partial-cleanup dir.
    """
    root = tmp_path / "logs"
    real = root / "20260503" / "1430_real" / "app1" / "raw"
    real.mkdir(parents=True)
    (real / "app.1.log").write_text("ok\n", encoding="utf-8")
    # Stray non-conforming siblings under the same date dir.
    (root / "20260503" / ".tmp").mkdir()
    (root / "20260503" / "scratch").mkdir()
    (root / "20260503" / "12_short").mkdir()  # too short, missing minute digits
    (root / "20260503" / "abcd_letters").mkdir()  # non-digit prefix

    runs = resolve_runs("all", root)
    assert [r.name for r in runs] == ["1430_real"]


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


def test_iter_lines_corrupt_zip_yields_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "raw.zip").write_bytes(b"not actually a zip file")
    lines = list(iter_lines(app))
    err = capsys.readouterr().err
    assert lines == []
    assert "skipping unreadable" in err
    assert "raw.zip" in err


def test_iter_lines_reads_both_raw_dir_and_raw_zip(tmp_path: Path) -> None:
    """Lock in the verbatim reference contract: when an app dir contains BOTH
    ``raw/`` and ``raw.zip`` (e.g. a partial cleanup), iter_lines surfaces lines
    from each. Changing this to a precedence rule would be a behaviour change.
    """
    app = tmp_path / "app"
    raw_dir = app / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "live.log").write_text("LIVE marker\n", encoding="utf-8")
    with zipfile.ZipFile(app / "raw.zip", "w") as zf:
        zf.writestr("archived.log", "ARCHIVED marker\n")

    sources = {source for source, _ in iter_lines(app)}
    lines = {line for _, line in iter_lines(app)}
    assert sources == {"live.log", "archived.log"}
    assert "LIVE marker" in lines
    assert "ARCHIVED marker" in lines


def test_search_continues_past_corrupt_zip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "logs"
    # Run with corrupt zip
    bad_app = root / "20260503" / "1430_bad" / "app1"
    bad_app.mkdir(parents=True)
    (bad_app / "raw.zip").write_bytes(b"truncated archive")
    # Later run with valid raw/ content
    good_log = root / "20260503" / "1500_good" / "app1" / "raw" / "app.1.log"
    good_log.parent.mkdir(parents=True)
    good_log.write_text("panic: real match\n", encoding="utf-8")

    rc = main(["search", "--root", str(root), "--run", "all", "--keyword", "panic"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "panic: real match" in captured.out
    assert "skipping unreadable" in captured.err
    assert "# total matches: 1" in captured.err


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


def test_iter_lines_skips_corrupt_zip_member(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One corrupted entry inside an otherwise-readable archive is skipped with
    a warning; sibling entries still yield their lines.
    """
    app = tmp_path / "app"
    app.mkdir()
    raw_zip = app / "raw.zip"
    with zipfile.ZipFile(raw_zip, "w") as zf:
        zf.writestr("good.log", "good line\n")
        zf.writestr("bad.log", "bad line\n")

    real_open = zipfile.ZipFile.open

    def fake_open(self: zipfile.ZipFile, name: Any, *args: Any, **kwargs: Any) -> Any:
        member_name = name.filename if hasattr(name, "filename") else name
        if member_name == "bad.log":
            raise zipfile.BadZipFile("simulated CRC failure")
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fake_open)
    lines = list(iter_lines(app))
    err = capsys.readouterr().err
    sources = [name for name, _ in lines]
    assert "good.log" in sources
    assert "bad.log" not in sources
    assert "skipping unreadable member" in err
    assert "bad.log" in err


def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGINT during a search returns exit code 130 (the documented contract)."""
    import paperbark.search as search_mod

    def _boom(_args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(search_mod, "run", _boom)
    rc = main(["search", "--keyword", "panic"])
    assert rc == 130
