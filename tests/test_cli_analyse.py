"""Tests for the CLI glue around ``paperbark analyse``.

Covers the argparse-to-AnalyseConfig override path plus an end-to-end
TOML-threading sanity check via :func:`paperbark.cli.main`. The analyse
runner itself is exercised in ``test_analyse.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from paperbark.cli import _merge_analyse_overrides, main
from paperbark.config import AnalyseConfig


def _ns(**overrides: object) -> argparse.Namespace:
    """Build an argparse.Namespace with the analyse flag defaults.

    Mirrors what argparse produces when each flag is omitted: ``None`` for the
    overrides we recognise. Tests then layer in whatever overrides they want.
    """
    base: dict[str, object] = {
        "run": None,
        "root": None,
        "app": None,
        "keyword": None,
        "regex": None,
        "out": None,
        "stdout": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_merge_returns_base_when_no_flags_set() -> None:
    base = AnalyseConfig(
        run="all",
        app="web",
        keywords=("panic",),
        regexes=(r"5\d\d",),
        out="reports/x",
        stdout=True,
    )
    assert _merge_analyse_overrides(base, _ns()) == base


def test_merge_applies_each_override() -> None:
    # All flags set → each maps cleanly onto AnalyseConfig fields. The CLI
    # ``--keyword`` (action=append) replaces TOML keywords rather than extending,
    # and ``--out ''`` is the documented "clear TOML override" sentinel.
    base = AnalyseConfig(
        run="latest",
        app="api",
        keywords=("panic", "fatal"),
        regexes=(r"5\d\d",),
        out="reports/x",
        stdout=False,
    )
    result = _merge_analyse_overrides(
        base,
        _ns(run="all", keyword=["custom"], stdout=True, out=""),
    )
    assert result.run == "all"
    assert result.keywords == ("custom",)
    assert result.stdout is True
    assert result.out == ""


def test_no_stdout_flag_parses() -> None:
    """``argparse.BooleanOptionalAction`` exposes the ``--no-stdout`` form so a
    TOML ``stdout = true`` can be cleared at the CLI without editing the file.
    """
    from paperbark.cli import _build_parser

    parser = _build_parser()
    assert parser.parse_args(["analyse", "--no-stdout"]).stdout is False
    assert parser.parse_args(["analyse", "--stdout"]).stdout is True
    assert parser.parse_args(["analyse"]).stdout is None  # falls through to TOML


# --- end-to-end TOML threading --------------------------------------------


def test_analyse_consumes_toml_keywords(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A TOML-supplied keyword surfaces in the rendered analysis even when the
    CLI passes no ``--keyword`` flag — proves [analyse].keywords is threaded
    through to the runner.
    """
    root = tmp_path / "logs"
    run_dir = root / "20260503" / "1430_demo" / "demo-app" / "raw"
    run_dir.mkdir(parents=True)
    (run_dir / "sample.log").write_text(
        "panic: db down\nINFO ok\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text(
        f'[paperbark]\nroot = "{root.as_posix()}"\n\n[analyse]\nkeywords = ["panic"]\n',
        encoding="utf-8",
    )

    rc = main(["analyse", "--config", str(config_path)])
    assert rc == 0
    md = (root / "20260503" / "1430_demo" / "analysis.md").read_text(encoding="utf-8")
    assert "panic" in md.lower()


def test_analyse_cli_root_overrides_toml(tmp_path: Path) -> None:
    """``--root`` overrides ``[paperbark].root`` for analyse."""
    real_root = tmp_path / "real"
    run_dir = real_root / "20260503" / "1430_demo" / "demo-app" / "raw"
    run_dir.mkdir(parents=True)
    (run_dir / "sample.log").write_text("INFO ok\n", encoding="utf-8")

    config_path = tmp_path / "paperbark.toml"
    config_path.write_text(
        f'[paperbark]\nroot = "{(tmp_path / "missing").as_posix()}"\n',
        encoding="utf-8",
    )

    rc = main(
        [
            "analyse",
            "--config",
            str(config_path),
            "--root",
            str(real_root),
        ]
    )
    assert rc == 0
    assert (real_root / "20260503" / "1430_demo" / "analysis.md").exists()


def test_analyse_config_error_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text('[analyse]\nstdout = "yes"\n', encoding="utf-8")
    rc = main(["analyse", "--config", str(config_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "config error" in err
