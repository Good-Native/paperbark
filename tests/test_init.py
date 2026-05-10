"""Tests for ``paperbark.init`` and the ``paperbark init`` CLI dispatch."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from paperbark.cli import main
from paperbark.config import PROBE_NAMES, Config, from_dict
from paperbark.init import STARTER_TOML, write_starter


def test_starter_round_trips_to_defaults() -> None:
    """The emitted template parses to ``Config.defaults()`` exactly AND lists
    every recognised probe key explicitly. The first assertion alone wouldn't
    catch a silently-omitted probe — ``ProbesConfig`` dataclass defaults fill
    in any missing key, so equality would still pass — hence the explicit
    key-coverage check on the raw dict.
    """
    raw = tomllib.loads(STARTER_TOML)
    parsed = from_dict(raw)
    assert parsed == Config.defaults()
    assert set(PROBE_NAMES).issubset(raw["probes"])


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


def test_cli_init_honours_explicit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Detection scans CWD, so chdir into the empty tmp_path to make
    # this test independent of where pytest was launched from.
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "custom.toml"
    rc = main(["init", "--path", str(target)])
    captured = capsys.readouterr()
    assert rc == 0
    assert target.read_text(encoding="utf-8") == STARTER_TOML
    assert str(target) in captured.err


def test_cli_init_refuses_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "paperbark.toml"
    target.write_text("# existing content\n", encoding="utf-8")
    rc = main(["init", "--path", str(target)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "already exists" in captured.err
    assert "--force" in captured.err
    # Existing content preserved.
    assert target.read_text(encoding="utf-8") == "# existing content\n"


def test_cli_init_force_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "paperbark.toml"
    target.write_text("# existing content\n", encoding="utf-8")
    rc = main(["init", "--path", str(target), "--force"])
    captured = capsys.readouterr()
    assert rc == 0
    assert target.read_text(encoding="utf-8") == STARTER_TOML
    assert "Wrote starter config" in captured.err


def test_cli_init_unwritable_target_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Writing into a path whose parent is a regular file raises OSError →
    exit 2 with a descriptive stderr message.
    """
    monkeypatch.chdir(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    target = blocker / "paperbark.toml"
    rc = main(["init", "--path", str(target)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Could not write" in err


def test_cli_init_detects_fly_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fly.toml").write_text(
        'app = "scratch-app"\nprimary_region = "syd"\n',
        encoding="utf-8",
    )
    rc = main(["init"])
    err = capsys.readouterr().err
    assert rc == 0
    written = (tmp_path / "paperbark.toml").read_text(encoding="utf-8")
    assert 'type = "flyctl"' in written
    assert 'app = "scratch-app"' in written
    # The bare placeholder block must not also be present, otherwise
    # the user would see two competing source examples.
    assert "# [[sources]]" not in written
    assert "Detected source(s): flyctl (app=scratch-app)" in err
    # Round-trip: the file must parse via the real config loader so a
    # follow-up ``paperbark monitor`` can load it.
    raw = tomllib.loads(written)
    parsed = from_dict(raw)
    assert len(parsed.sources) == 1
    assert parsed.sources[0].type == "flyctl"
    assert parsed.sources[0].options == {"app": "scratch-app"}


def test_cli_init_detects_wrangler_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wrangler.toml").write_text(
        'name = "edge-worker"\naccount_id = "abc123"\n',
        encoding="utf-8",
    )
    rc = main(["init"])
    err = capsys.readouterr().err
    assert rc == 0
    written = (tmp_path / "paperbark.toml").read_text(encoding="utf-8")
    assert 'type = "wrangler"' in written
    assert 'worker = "edge-worker"' in written
    assert 'account_id = "abc123"' in written
    assert "Detected source(s): wrangler (worker=edge-worker)" in err
    raw = tomllib.loads(written)
    parsed = from_dict(raw)
    assert len(parsed.sources) == 1
    assert parsed.sources[0].type == "wrangler"
    assert parsed.sources[0].options == {
        "worker": "edge-worker",
        "account_id": "abc123",
    }


def test_cli_init_detects_both_manifests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fly.toml").write_text('app = "fly-side"\n', encoding="utf-8")
    (tmp_path / "wrangler.toml").write_text('name = "wrangler-side"\n', encoding="utf-8")
    rc = main(["init"])
    assert rc == 0
    written = (tmp_path / "paperbark.toml").read_text(encoding="utf-8")
    raw = tomllib.loads(written)
    parsed = from_dict(raw)
    types = sorted(s.type for s in parsed.sources)
    assert types == ["flyctl", "wrangler"]
    names = {s.name for s in parsed.sources}
    assert names == {"fly", "wrangler"}


def test_cli_init_escapes_special_chars_in_detected_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker/app name containing a quote, backslash, or newline must
    not break the generated TOML. Real wrangler/fly names don't allow
    these characters, but the writer shouldn't trust manifest input —
    a hand-edited or malformed manifest must still produce parseable
    output (or fail loudly at parse time, never silently corrupt)."""
    monkeypatch.chdir(tmp_path)
    # tomllib accepts this as a single-line basic string with the
    # double-quote inside, so the manifest itself parses fine — the
    # question is whether our writer escapes it on the way back out.
    (tmp_path / "wrangler.toml").write_text(
        'name = "weird\\"name"\n',
        encoding="utf-8",
    )
    rc = main(["init"])
    assert rc == 0
    written = (tmp_path / "paperbark.toml").read_text(encoding="utf-8")
    # If the writer interpolated raw, the file would contain
    # ``worker = "weird"name"`` which is invalid TOML. tomllib raising
    # here would be the canary.
    raw = tomllib.loads(written)
    parsed = from_dict(raw)
    assert parsed.sources[0].options["worker"] == 'weird"name'


def test_cli_init_no_detect_emits_bare_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-detect`` opts out even when a manifest is present, so users
    who want the commented-out example back can still get it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fly.toml").write_text('app = "should-be-ignored"\n', encoding="utf-8")
    rc = main(["init", "--no-detect"])
    assert rc == 0
    assert (tmp_path / "paperbark.toml").read_text(encoding="utf-8") == STARTER_TOML
