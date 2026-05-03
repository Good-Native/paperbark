"""Smoke tests so CI is green from day one."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from paperbark import __version__
from paperbark.cli import main
from paperbark.config import Config


def test_version_string_is_pep440() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", __version__)


def test_cli_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out


def test_cli_help_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "paperbark" in captured.out


def test_cli_default_command_is_monitor_with_no_sources_configured(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No subcommand defaults to 'monitor'. With an empty config (no
    # sources), the dispatcher fails closed with a clear stderr message
    # rather than silently proceeding. We patch ``load`` directly so the
    # test never touches the operator's real ``paperbark.toml``.
    monkeypatch.setattr("paperbark.config.load", lambda _path=None: Config())
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no sources configured" in captured.err


def test_cli_monitor_with_invalid_config_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ``--config`` pointing at a missing file surfaces as a typed config
    # error rather than a traceback.
    rc = main(["monitor", "--config", str(tmp_path / "nope.toml")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "config error" in captured.err
