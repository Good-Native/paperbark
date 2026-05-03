"""Tests for paperbark.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperbark.config import (
    Config,
    ConfigError,
    PatternOverride,
    ProbesConfig,
    SourceConfig,
    discover,
    from_dict,
    load,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_defaults_when_no_file_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # so ~/.config doesn't exist
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = load(cwd=tmp_path)
    assert config == Config.defaults()
    assert config.root == Path("logs")
    assert config.probes.is_enabled("severity")
    assert config.sources == ()


def test_load_reads_explicit_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "paperbark.toml",
        '[paperbark]\nroot = "captures"\n',
    )
    config = load(path)
    assert config.root == Path("captures")


def test_load_raises_when_explicit_path_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load(tmp_path / "missing.toml")


def test_load_raises_on_invalid_toml(tmp_path: Path) -> None:
    path = _write(tmp_path / "paperbark.toml", "not = valid toml [")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load(path)


def test_load_rejects_directory_as_path(tmp_path: Path) -> None:
    # If a path resolves to a directory we should fail closed via ConfigError
    # rather than letting a raw OSError escape from open().
    directory = tmp_path / "paperbark.toml"
    directory.mkdir()
    with pytest.raises(ConfigError, match="not found"):
        load(directory)


def test_from_dict_rejects_non_string_root() -> None:
    with pytest.raises(ConfigError, match=r"\[paperbark\]\.root must be a string"):
        from_dict({"paperbark": {"root": 123}})


def test_discover_prefers_cwd_over_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home_config = home / ".config" / "paperbark" / "config.toml"
    cwd_config = tmp_path / "cwd" / "paperbark.toml"
    _write(home_config, "")
    _write(cwd_config, "")
    monkeypatch.setattr(Path, "home", lambda: home)
    found = discover(cwd=tmp_path / "cwd")
    assert found == cwd_config


def test_discover_falls_back_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home_config = home / ".config" / "paperbark" / "config.toml"
    _write(home_config, "")
    monkeypatch.setattr(Path, "home", lambda: home)
    found = discover(cwd=tmp_path / "no-config-here")
    assert found == home_config


def test_discover_returns_none_when_nothing_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-home")
    assert discover(cwd=tmp_path / "no-cwd") is None


def test_from_dict_parses_minimal_config() -> None:
    config = from_dict({})
    assert config == Config.defaults()


def test_from_dict_parses_probe_toggles() -> None:
    config = from_dict({"probes": {"severity": False, "panics": False}})
    assert not config.probes.is_enabled("severity")
    assert not config.probes.is_enabled("panics")
    assert config.probes.is_enabled("http")  # untouched defaults


def test_from_dict_parses_keywords_and_regexes() -> None:
    config = from_dict(
        {
            "probes": {
                "keywords": ["panic", "fatal"],
                "regexes": [r"err\d+"],
            },
        }
    )
    assert config.probes.keywords == ("panic", "fatal")
    assert config.probes.regexes == (r"err\d+",)


def test_from_dict_parses_pattern_overrides() -> None:
    config = from_dict(
        {
            "probes": {
                "patterns": {
                    "autoscaler": [
                        {"label": "reconciling", "pattern": "reconciling app"},
                        {"label": "scale up", "pattern": "scaling up"},
                    ],
                },
            },
        }
    )
    overrides = config.probes.pattern_overrides["autoscaler"]
    assert overrides == (
        PatternOverride(label="reconciling", pattern="reconciling app"),
        PatternOverride(label="scale up", pattern="scaling up"),
    )


def test_from_dict_parses_sources() -> None:
    config = from_dict(
        {
            "sources": [
                {"name": "main", "type": "flyctl", "app": "fly-app-a"},
                {"name": "worker", "type": "flyctl", "app": "fly-worker", "no_tail": True},
            ],
        }
    )
    assert config.sources == (
        SourceConfig(name="main", type="flyctl", options={"app": "fly-app-a"}),
        SourceConfig(
            name="worker",
            type="flyctl",
            options={"app": "fly-worker", "no_tail": True},
        ),
    )


def test_duplicate_source_names_rejected() -> None:
    with pytest.raises(ConfigError, match="duplicate source name"):
        from_dict(
            {
                "sources": [
                    {"name": "main", "type": "flyctl"},
                    {"name": "main", "type": "flyctl"},
                ],
            }
        )


def test_source_missing_name_rejected() -> None:
    with pytest.raises(ConfigError, match="missing or invalid 'name'"):
        from_dict({"sources": [{"type": "flyctl"}]})


def test_source_missing_type_rejected() -> None:
    with pytest.raises(ConfigError, match="missing or invalid 'type'"):
        from_dict({"sources": [{"name": "main"}]})


def test_probe_toggle_must_be_bool() -> None:
    with pytest.raises(ConfigError, match="must be a boolean"):
        from_dict({"probes": {"severity": "yes"}})


def test_keywords_must_be_list_of_strings() -> None:
    with pytest.raises(ConfigError, match="must be a string"):
        from_dict({"probes": {"keywords": ["ok", 42]}})


def test_pattern_override_missing_label_rejected() -> None:
    with pytest.raises(ConfigError, match="missing or invalid 'label'"):
        from_dict({"probes": {"patterns": {"autoscaler": [{"pattern": "x"}]}}})


def test_pattern_override_missing_pattern_rejected() -> None:
    with pytest.raises(ConfigError, match="missing or invalid 'pattern'"):
        from_dict({"probes": {"patterns": {"autoscaler": [{"label": "x"}]}}})


def test_load_full_config_round_trip(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "paperbark.toml",
        """
[paperbark]
root = "captures"

[probes]
severity = true
panics = false
keywords = ["panic"]

[probes.patterns]
autoscaler = [
    { label = "reconciling", pattern = "reconciling app" },
]

[[sources]]
name = "main"
type = "flyctl"
app = "fly-app-a"
""",
    )
    config = load(path)
    assert config.root == Path("captures")
    assert config.probes.is_enabled("severity")
    assert not config.probes.is_enabled("panics")
    assert config.probes.keywords == ("panic",)
    assert config.probes.pattern_overrides == {
        "autoscaler": (PatternOverride(label="reconciling", pattern="reconciling app"),),
    }
    assert config.sources == (
        SourceConfig(name="main", type="flyctl", options={"app": "fly-app-a"}),
    )


def test_probes_config_is_enabled_returns_false_for_unknown() -> None:
    assert not ProbesConfig().is_enabled("not-a-real-probe")
