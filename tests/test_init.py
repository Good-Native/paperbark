"""Tests for ``paperbark.init`` and the ``paperbark init`` CLI dispatch."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from paperbark.cli import main
from paperbark.config import Config, from_dict
from paperbark.init import STARTER_TOML, write_starter


def test_starter_round_trips_to_defaults() -> None:
    """The emitted template parses to ``Config.defaults()`` exactly: no
    surprise enabled-by-the-template-but-not-by-default settings.
    """
    parsed = from_dict(tomllib.loads(STARTER_TOML))
    assert parsed == Config.defaults()


def test_write_starter_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "paperbark.toml"
    write_starter(target)
    assert target.read_text(encoding="utf-8") == STARTER_TOML


def test_write_starter_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper" / "paperbark.toml"
    write_starter(target)
    assert target.exists()


def test_cli_init_writes_to_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ``--path`` → writes ``./paperbark.toml`` relative to cwd."""
    monkeypatch.chdir(tmp_path)
    rc = main(["init"])
    captured = capsys.readouterr()
    assert rc == 0
    assert (tmp_path / "paperbark.toml").read_text(encoding="utf-8") == STARTER_TOML
    assert "Wrote starter config" in captured.err


def test_cli_init_honours_explicit_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "custom.toml"
    rc = main(["init", "--path", str(target)])
    captured = capsys.readouterr()
    assert rc == 0
    assert target.read_text(encoding="utf-8") == STARTER_TOML
    assert str(target) in captured.err


def test_cli_init_refuses_existing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "paperbark.toml"
    target.write_text("# existing content\n", encoding="utf-8")
    rc = main(["init", "--path", str(target)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "already exists" in captured.err
    assert "--force" in captured.err
    # Existing content preserved.
    assert target.read_text(encoding="utf-8") == "# existing content\n"


def test_cli_init_force_overwrites(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "paperbark.toml"
    target.write_text("# existing content\n", encoding="utf-8")
    rc = main(["init", "--path", str(target), "--force"])
    captured = capsys.readouterr()
    assert rc == 0
    assert target.read_text(encoding="utf-8") == STARTER_TOML
    assert "Wrote starter config" in captured.err


def test_cli_init_unwritable_target_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Writing into a path whose parent is a regular file raises OSError →
    exit 2 with a descriptive stderr message.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    target = blocker / "paperbark.toml"
    rc = main(["init", "--path", str(target)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Could not write" in err
