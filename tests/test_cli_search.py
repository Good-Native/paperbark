"""Tests for the CLI glue around ``paperbark search``.

Covers the argparse-to-SearchConfig override path plus an end-to-end
TOML-threading sanity check via :func:`paperbark.cli.main`. The search
runner itself is exercised in ``test_search.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from paperbark.cli import _merge_search_overrides, main
from paperbark.config import SearchConfig


def _ns(**overrides: object) -> argparse.Namespace:
    """Build an argparse.Namespace with the search flag defaults."""
    base: dict[str, object] = {
        "run": None,
        "root": None,
        "app": None,
        "keyword": None,
        "regex": None,
        "case_sensitive": None,
        "max": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_merge_returns_base_when_no_flags_set() -> None:
    base = SearchConfig(
        run="all",
        app="web",
        keywords=("panic",),
        regexes=(r"5\d\d",),
        case_sensitive=True,
        max=42,
    )
    assert _merge_search_overrides(base, _ns()) == base


def test_merge_keyword_replaces_toml() -> None:
    base = SearchConfig(keywords=("panic", "fatal"))
    result = _merge_search_overrides(base, _ns(keyword=["custom"]))
    assert result.keywords == ("custom",)


def test_merge_case_sensitive_flag_overrides() -> None:
    base = SearchConfig(case_sensitive=False)
    result = _merge_search_overrides(base, _ns(case_sensitive=True))
    assert result.case_sensitive is True


def test_merge_max_zero_means_unlimited() -> None:
    base = SearchConfig(max=10)
    result = _merge_search_overrides(base, _ns(max=0))
    assert result.max == 0


def test_merge_rejects_negative_max() -> None:
    with pytest.raises(ValueError, match=r"--max must be >= 0"):
        _merge_search_overrides(SearchConfig(), _ns(max=-1))


def test_merge_run_override() -> None:
    result = _merge_search_overrides(SearchConfig(), _ns(run="20260503"))
    assert result.run == "20260503"


# --- end-to-end TOML threading --------------------------------------------


def test_search_consumes_toml_keywords(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A TOML-supplied keyword drives matching even with no CLI ``--keyword``.

    Pre-PR, search exited 2 ("Provide at least one --keyword or --regex") in
    this scenario; threading [search].keywords through is the substantive
    behaviour change.
    """
    root = tmp_path / "logs"
    raw = root / "20260503" / "1430_demo" / "demo-app" / "raw"
    raw.mkdir(parents=True)
    (raw / "sample.log").write_text(
        "panic: db down\nINFO ok\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text(
        f'[paperbark]\nroot = "{root.as_posix()}"\n\n[search]\nkeywords = ["panic"]\n',
        encoding="utf-8",
    )

    rc = main(["search", "--config", str(config_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "panic: db down" in captured.out


def test_search_cli_keyword_replaces_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A CLI ``--keyword`` replaces the TOML default rather than extending it."""
    root = tmp_path / "logs"
    raw = root / "20260503" / "1430_demo" / "demo-app" / "raw"
    raw.mkdir(parents=True)
    (raw / "sample.log").write_text(
        "panic: db down\nslow request 1234ms\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text(
        f'[paperbark]\nroot = "{root.as_posix()}"\n\n[search]\nkeywords = ["panic"]\n',
        encoding="utf-8",
    )

    rc = main(["search", "--config", str(config_path), "--keyword", "slow"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "slow request" in captured.out
    assert "panic: db down" not in captured.out


def test_search_negative_max_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text("", encoding="utf-8")
    rc = main(["search", "--config", str(config_path), "--keyword", "x", "--max", "-1"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "search error" in err
    assert "--max" in err
