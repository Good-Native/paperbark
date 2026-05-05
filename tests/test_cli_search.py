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


def test_merge_applies_each_override() -> None:
    # CLI ``--keyword`` replaces (not extends) the TOML keyword list; ``--max 0``
    # is the documented "unlimited" sentinel and must round-trip.
    base = SearchConfig(keywords=("panic", "fatal"), case_sensitive=False, max=10)
    result = _merge_search_overrides(
        base,
        _ns(run="20260503", keyword=["custom"], case_sensitive=True, max=0),
    )
    assert result.run == "20260503"
    assert result.keywords == ("custom",)
    assert result.case_sensitive is True
    assert result.max == 0


def test_merge_rejects_negative_max() -> None:
    with pytest.raises(ValueError, match=r"--max must be >= 0"):
        _merge_search_overrides(SearchConfig(), _ns(max=-1))


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


def test_ignore_case_flag_overrides_toml_case_sensitive_true(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--ignore-case`` must clear a TOML ``[search].case_sensitive = true``.

    Pre-fix the flag was inert (it set a separate ``args.ignore_case`` dest
    that ``paperbark.search.run`` never read), so a TOML-true plus a CLI
    ``--ignore-case`` left matching case-sensitive. The mutex group fix in
    cli._build_parser ties both flags to the ``case_sensitive`` dest so the
    CLI can clear the TOML override at runtime.
    """
    root = tmp_path / "logs"
    raw = root / "20260503" / "1430_demo" / "demo-app" / "raw"
    raw.mkdir(parents=True)
    # Mixed-case sample: TOML ``case_sensitive = true`` would only match
    # "PANIC", while ``--ignore-case`` should match both lines.
    (raw / "sample.log").write_text(
        "panic: lower\nPANIC: upper\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text(
        f'[paperbark]\nroot = "{root.as_posix()}"\n\n'
        "[search]\n"
        'keywords = ["panic"]\n'
        "case_sensitive = true\n",
        encoding="utf-8",
    )

    rc = main(["search", "--config", str(config_path), "--ignore-case"])
    captured = capsys.readouterr()
    assert rc == 0
    # Both lines must surface — proving the CLI flag overrode TOML.
    assert "panic: lower" in captured.out
    assert "PANIC: upper" in captured.out


def test_case_sensitive_flag_overrides_toml_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--case-sensitive`` must enforce strict matching even when TOML omits it."""
    root = tmp_path / "logs"
    raw = root / "20260503" / "1430_demo" / "demo-app" / "raw"
    raw.mkdir(parents=True)
    (raw / "sample.log").write_text(
        "panic: lower\nPANIC: upper\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text(
        f'[paperbark]\nroot = "{root.as_posix()}"\n',
        encoding="utf-8",
    )

    rc = main(
        [
            "search",
            "--config",
            str(config_path),
            "--keyword",
            "PANIC",
            "--case-sensitive",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "PANIC: upper" in captured.out
    assert "panic: lower" not in captured.out


def test_ignore_case_and_case_sensitive_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """argparse rejects ``--ignore-case`` and ``--case-sensitive`` together."""
    with pytest.raises(SystemExit):
        main(
            [
                "search",
                "--keyword",
                "x",
                "--ignore-case",
                "--case-sensitive",
            ]
        )
    err = capsys.readouterr().err
    assert "not allowed with argument" in err


def test_search_negative_max_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "paperbark.toml"
    config_path.write_text("", encoding="utf-8")
    rc = main(["search", "--config", str(config_path), "--keyword", "x", "--max", "-1"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "search error" in err
    assert "--max" in err
