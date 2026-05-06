"""Tests for paperbark.autoupdate."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from paperbark import autoupdate


class _TTYBuffer(io.StringIO):
    """StringIO that claims to be a TTY so prompt mode kicks in."""

    def isatty(self) -> bool:
        return True


def _stub_fetch(record: list[str], result: str | None) -> Any:
    """Return a fetch stub that records each call and returns ``result``."""

    def _stub() -> str | None:
        record.append("hit")
        return result

    return _stub


def _stub_readline(record: list[str], answer: str) -> Any:
    """Return a readline stub that records each call and returns ``answer``."""

    def _stub(stdin: Any, timeout: float) -> str:
        record.append("ask")
        return answer

    return _stub


def _common_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "enabled": True,
        "mode": "prompt",
        "check_interval_hours": 24,
        "stdin": _TTYBuffer(),
        "stdout": _TTYBuffer(),
        "stderr": _TTYBuffer(),
        "argv": ["paperbark", "monitor"],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the cache file to a tmpdir so tests can't leak into ~/.cache."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.delenv("PAPERBARK_NO_AUTO_UPDATE", raising=False)
    return cache_dir / "paperbark" / "last_check.json"


@pytest.fixture(autouse=True)
def _force_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend we're running from a normal install, not a source checkout."""
    monkeypatch.setattr(autoupdate, "_is_editable_install", lambda: False)


def test_disabled_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", _stub_fetch(called, "9.9.9"))
    autoupdate.maybe_run(**_common_kwargs(enabled=False))
    assert called == []


def test_off_mode_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", _stub_fetch(called, "9.9.9"))
    autoupdate.maybe_run(**_common_kwargs(mode="off"))
    assert called == []


def test_env_var_skips_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERBARK_NO_AUTO_UPDATE", "1")
    called: list[str] = []
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", _stub_fetch(called, "9.9.9"))
    autoupdate.maybe_run(**_common_kwargs())
    assert called == []


def test_no_newer_version_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.0.0")
    out = _TTYBuffer()
    err = _TTYBuffer()
    autoupdate.maybe_run(**_common_kwargs(stdout=out, stderr=err))
    assert out.getvalue() == ""
    assert err.getvalue() == ""


def test_notify_mode_prints_to_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.2.0")
    err = _TTYBuffer()
    autoupdate.maybe_run(**_common_kwargs(mode="notify", stderr=err))
    text = err.getvalue()
    assert "1.2.0 available" in text
    assert "1.0.0" in text


def test_prompt_falls_back_to_notify_without_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.2.0")
    plain_in = io.StringIO("")  # no isatty override → False
    plain_out = io.StringIO()
    err = _TTYBuffer()
    autoupdate.maybe_run(**_common_kwargs(stdin=plain_in, stdout=plain_out, stderr=err))
    assert plain_out.getvalue() == ""
    assert "1.2.0 available" in err.getvalue()


def test_prompt_accept_runs_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.2.0")
    monkeypatch.setattr(autoupdate, "_readline_with_timeout", lambda stdin, timeout: "y\n")
    calls: list[list[str] | None] = []

    def _fake_upgrade(stdout: Any, stderr: Any, argv: list[str] | None) -> None:
        calls.append(argv)

    monkeypatch.setattr(autoupdate, "_run_upgrade_and_relaunch", _fake_upgrade)
    autoupdate.maybe_run(**_common_kwargs())
    assert calls == [["paperbark", "monitor"]]


def test_prompt_decline_records_version(
    monkeypatch: pytest.MonkeyPatch, _isolated_cache: Path
) -> None:
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.2.0")
    monkeypatch.setattr(autoupdate, "_readline_with_timeout", lambda stdin, timeout: "n\n")
    upgraded: list[Any] = []
    monkeypatch.setattr(
        autoupdate,
        "_run_upgrade_and_relaunch",
        lambda *a, **k: upgraded.append(a),
    )
    autoupdate.maybe_run(**_common_kwargs())
    assert upgraded == []
    payload = json.loads(_isolated_cache.read_text())
    assert payload["declined_version"] == "1.2.0"


def test_prompt_skipped_for_previously_declined(
    monkeypatch: pytest.MonkeyPatch, _isolated_cache: Path
) -> None:
    _isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    _isolated_cache.write_text(
        json.dumps(
            {
                "last_check": time.time(),
                "latest_version": "1.2.0",
                "declined_version": "1.2.0",
            }
        )
    )
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    fetch_calls: list[str] = []
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", _stub_fetch(fetch_calls, "1.2.0"))
    asked: list[str] = []
    monkeypatch.setattr(autoupdate, "_readline_with_timeout", _stub_readline(asked, "y\n"))
    autoupdate.maybe_run(**_common_kwargs())
    assert asked == []
    # Cache was fresh, so we shouldn't even hit PyPI.
    assert fetch_calls == []


def test_decline_cleared_when_newer_release_appears(
    monkeypatch: pytest.MonkeyPatch, _isolated_cache: Path
) -> None:
    _isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    _isolated_cache.write_text(
        json.dumps(
            {
                "last_check": 0,  # stale: forces a refresh
                "latest_version": "1.2.0",
                "declined_version": "1.2.0",
            }
        )
    )
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.3.0")
    monkeypatch.setattr(autoupdate, "_readline_with_timeout", lambda stdin, timeout: "n\n")
    monkeypatch.setattr(autoupdate, "_run_upgrade_and_relaunch", lambda *a, **k: None)
    autoupdate.maybe_run(**_common_kwargs())
    payload = json.loads(_isolated_cache.read_text())
    # Declined version should have rolled forward to 1.3.0 (the new prompt).
    assert payload["declined_version"] == "1.3.0"
    assert payload["latest_version"] == "1.3.0"


def test_assume_yes_skips_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: "1.2.0")
    asked: list[str] = []
    monkeypatch.setattr(autoupdate, "_readline_with_timeout", _stub_readline(asked, "n\n"))
    upgraded: list[Any] = []

    def _capture(*args: Any, **_kwargs: Any) -> None:
        upgraded.append(args)

    monkeypatch.setattr(autoupdate, "_run_upgrade_and_relaunch", _capture)
    autoupdate.maybe_run(**_common_kwargs(assume_yes=True))
    assert asked == []
    assert len(upgraded) == 1


def test_cache_freshness_avoids_network(
    monkeypatch: pytest.MonkeyPatch, _isolated_cache: Path
) -> None:
    _isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    _isolated_cache.write_text(json.dumps({"last_check": time.time(), "latest_version": "1.0.0"}))
    fetched: list[str] = []
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", _stub_fetch(fetched, "9.9.9"))
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    autoupdate.maybe_run(**_common_kwargs(mode="notify"))
    assert fetched == []


def test_network_failure_uses_cached_version(
    monkeypatch: pytest.MonkeyPatch, _isolated_cache: Path
) -> None:
    _isolated_cache.parent.mkdir(parents=True, exist_ok=True)
    _isolated_cache.write_text(json.dumps({"last_check": 0, "latest_version": "1.5.0"}))
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", lambda: None)
    monkeypatch.setattr(autoupdate, "__version__", "1.0.0")
    err = _TTYBuffer()
    autoupdate.maybe_run(**_common_kwargs(mode="notify", stderr=err))
    assert "1.5.0 available" in err.getvalue()


def test_editable_install_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autoupdate, "_is_editable_install", lambda: True)
    fetched: list[str] = []
    monkeypatch.setattr(autoupdate, "_fetch_pypi_version", _stub_fetch(fetched, "9.9.9"))
    autoupdate.maybe_run(**_common_kwargs())
    assert fetched == []


def test_is_newer_handles_unparseable_versions() -> None:
    assert autoupdate._is_newer("1.2.0", "1.0.0") is True
    assert autoupdate._is_newer("1.0.0", "1.0.0") is False
    assert autoupdate._is_newer("0.9.0", "1.0.0") is False


def test_detect_upgrade_command_pipx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "paperbark.autoupdate.sys.executable",
        "/Users/x/.local/pipx/venvs/paperbark/bin/python",
    )
    monkeypatch.setattr(
        "paperbark.autoupdate.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    cmd = autoupdate._detect_upgrade_command()
    assert cmd == ["/usr/bin/pipx", "upgrade", "paperbark"]


def test_detect_upgrade_command_pip_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_python = tmp_path / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("")
    monkeypatch.setattr("paperbark.autoupdate.sys.executable", str(fake_python))
    cmd = autoupdate._detect_upgrade_command()
    assert cmd is not None
    assert cmd[1:] == ["-m", "pip", "install", "--upgrade", "paperbark"]
