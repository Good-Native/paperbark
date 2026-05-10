"""Tests for ``paperbark.detect`` — manifest-driven source autodetection."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperbark.detect import DetectedSource, detect


def test_empty_dir_returns_no_sources(tmp_path: Path) -> None:
    assert detect(tmp_path) == []


def test_fly_toml_detected(tmp_path: Path) -> None:
    (tmp_path / "fly.toml").write_text(
        'app = "harvey-prod"\nprimary_region = "syd"\n',
        encoding="utf-8",
    )
    assert detect(tmp_path) == [
        DetectedSource(name="fly", type="flyctl", app="harvey-prod"),
    ]


def test_fly_toml_legacy_app_name_key(tmp_path: Path) -> None:
    """Pre-2023 fly.toml files used ``app_name`` instead of ``app`` —
    we fall through to the legacy key so old projects still work."""
    (tmp_path / "fly.toml").write_text('app_name = "legacy-app"\n', encoding="utf-8")
    assert detect(tmp_path) == [
        DetectedSource(name="fly", type="flyctl", app="legacy-app"),
    ]


def test_fly_toml_missing_app_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "fly.toml").write_text('primary_region = "syd"\n', encoding="utf-8")
    assert detect(tmp_path) == []
    err = capsys.readouterr().err
    assert "no top-level 'app'" in err


def test_fly_toml_malformed_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "fly.toml").write_text("app = =broken\n", encoding="utf-8")
    assert detect(tmp_path) == []
    err = capsys.readouterr().err
    assert "could not parse fly.toml" in err


def test_wrangler_toml_detected(tmp_path: Path) -> None:
    (tmp_path / "wrangler.toml").write_text(
        'name = "edge-worker"\naccount_id = "abc123"\n',
        encoding="utf-8",
    )
    assert detect(tmp_path) == [
        DetectedSource(
            name="wrangler",
            type="wrangler",
            worker="edge-worker",
            account_id="abc123",
        ),
    ]


def test_wrangler_toml_without_account_id(tmp_path: Path) -> None:
    (tmp_path / "wrangler.toml").write_text('name = "edge-worker"\n', encoding="utf-8")
    assert detect(tmp_path) == [
        DetectedSource(name="wrangler", type="wrangler", worker="edge-worker"),
    ]


def test_wrangler_jsonc_with_comments_and_trailing_commas(tmp_path: Path) -> None:
    """JSONC parsing must survive both line/block comments and trailing
    commas — wrangler's own scaffold output uses both. The comment
    stripper also has to leave ``//`` inside strings alone, which is
    why the URL value is here."""
    (tmp_path / "wrangler.jsonc").write_text(
        """
        {
          // top-level worker name
          "name": "jsonc-worker",
          /* multi-line
             comment */
          "account_id": "def456",
          "compatibility_date": "2026-01-01",
          "vars": {
            "DOCS_URL": "https://example.com/docs",
          },
        }
        """,
        encoding="utf-8",
    )
    assert detect(tmp_path) == [
        DetectedSource(
            name="wrangler",
            type="wrangler",
            worker="jsonc-worker",
            account_id="def456",
        ),
    ]


def test_wrangler_toml_preferred_over_jsonc(tmp_path: Path) -> None:
    """Both manifests present → TOML wins (matches wrangler 4.x's own
    resolution order)."""
    (tmp_path / "wrangler.toml").write_text('name = "from-toml"\n', encoding="utf-8")
    (tmp_path / "wrangler.jsonc").write_text('{"name": "from-jsonc"}\n', encoding="utf-8")
    assert detect(tmp_path) == [
        DetectedSource(name="wrangler", type="wrangler", worker="from-toml"),
    ]


def test_wrangler_jsonc_malformed_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "wrangler.jsonc").write_text("{not json", encoding="utf-8")
    assert detect(tmp_path) == []
    err = capsys.readouterr().err
    assert "could not parse wrangler.jsonc" in err


def test_both_fly_and_wrangler_detected(tmp_path: Path) -> None:
    """Combined project: order is deterministic (fly first), names are
    distinct so :func:`paperbark.config.from_dict` won't reject the
    pair as duplicates."""
    (tmp_path / "fly.toml").write_text('app = "fly-side"\n', encoding="utf-8")
    (tmp_path / "wrangler.toml").write_text('name = "wrangler-side"\n', encoding="utf-8")
    detected = detect(tmp_path)
    assert detected == [
        DetectedSource(name="fly", type="flyctl", app="fly-side"),
        DetectedSource(name="wrangler", type="wrangler", worker="wrangler-side"),
    ]
    assert len({d.name for d in detected}) == len(detected)


def test_plain_wrangler_json_detected(tmp_path: Path) -> None:
    """Wrangler 4.x also reads plain ``wrangler.json`` (no comments)."""
    (tmp_path / "wrangler.json").write_text('{"name": "plain-json"}\n', encoding="utf-8")
    assert detect(tmp_path) == [
        DetectedSource(name="wrangler", type="wrangler", worker="plain-json"),
    ]
