"""Smoke tests so CI is green from day one."""

from __future__ import annotations

import re

import pytest

from paperbark import __version__
from paperbark.cli import main


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


def test_cli_unknown_subcommand_falls_through_to_monitor_stub(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No subcommand should default to the 'monitor' stub.
    rc = main([])
    captured = capsys.readouterr()
    assert rc != 0
    assert "monitor" in captured.err
