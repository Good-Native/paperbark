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


def test_merge_run_override() -> None:
    result = _merge_analyse_overrides(AnalyseConfig(), _ns(run="all"))
    assert result.run == "all"


def test_merge_keyword_replaces_toml() -> None:
    # Append-action lists are full overrides, not extensions: a CLI ``--keyword
    # foo`` replaces the TOML keyword set so you can narrow searches without
    # editing the file.
    base = AnalyseConfig(keywords=("panic", "fatal"))
    result = _merge_analyse_overrides(base, _ns(keyword=["custom"]))
    assert result.keywords == ("custom",)


def test_merge_keyword_none_keeps_toml() -> None:
    base = AnalyseConfig(keywords=("panic",))
    result = _merge_analyse_overrides(base, _ns(keyword=None))
    assert result.keywords == ("panic",)


def test_merge_stdout_flag_overrides_toml() -> None:
    base = AnalyseConfig(stdout=False)
    result = _merge_analyse_overrides(base, _ns(stdout=True))
    assert result.stdout is True


def test_merge_out_blank_clears_toml_default() -> None:
    # The CLI passes "" only when the user explicitly types --out '' which is
    # a documented way to clear a TOML override and fall back to the default
    # ``<run>/analysis`` base. argparse default is None (skip).
    base = AnalyseConfig(out="reports/x")
    result = _merge_analyse_overrides(base, _ns(out=""))
    assert result.out == ""


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
