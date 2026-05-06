"""Tests for paperbark.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperbark.config import (
    DEFAULT_ANALYSE_EVERY,
    DEFAULT_INTERVAL,
    DEFAULT_ITERATIONS,
    AnalyseConfig,
    Config,
    ConfigError,
    MonitorConfig,
    PatternOverride,
    ProbesConfig,
    SearchConfig,
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


def test_from_dict_rejects_non_mapping_root() -> None:
    # Programmatic callers passing a list / scalar should hit ConfigError, not
    # AttributeError — keeping the validation contract typed.
    for bad in ([1, 2, 3], "string", 42, None):
        with pytest.raises(ConfigError, match="config root must be a table"):
            from_dict(bad)  # type: ignore[arg-type]


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


def test_discover_skips_directory_with_config_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A directory named `paperbark.toml` in cwd should not mask the home config.
    home = tmp_path / "home"
    home_config = home / ".config" / "paperbark" / "config.toml"
    _write(home_config, "")
    cwd = tmp_path / "cwd"
    (cwd / "paperbark.toml").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    assert discover(cwd=cwd) == home_config


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


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"sources": [{"type": "flyctl"}]}, "missing or invalid 'name'"),
        ({"sources": [{"name": "main"}]}, "missing or invalid 'type'"),
        ({"probes": {"severity": "yes"}}, "must be a boolean"),
        ({"probes": {"keywords": ["ok", 42]}}, "must be a string"),
        (
            {"probes": {"patterns": {"autoscaler": [{"pattern": "x"}]}}},
            "missing or invalid 'label'",
        ),
        (
            {"probes": {"patterns": {"autoscaler": [{"label": "x"}]}}},
            "missing or invalid 'pattern'",
        ),
    ],
)
def test_from_dict_rejects_malformed_payload(payload: dict[str, object], expected: str) -> None:
    with pytest.raises(ConfigError, match=expected):
        from_dict(payload)


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


def test_probes_config_is_enabled_rejects_non_probe_attributes() -> None:
    # `keywords` is a real attribute on ProbesConfig but it's not a probe flag,
    # so its truthiness must not leak through is_enabled.
    config = ProbesConfig(keywords=("panic",))
    assert not config.is_enabled("keywords")
    assert not config.is_enabled("regexes")
    assert not config.is_enabled("pattern_overrides")


# --- monitor ---------------------------------------------------------------


def test_monitor_defaults_match_bash_dispatcher() -> None:
    config = Config.defaults()
    assert config.monitor == MonitorConfig(
        interval=DEFAULT_INTERVAL,
        iterations=DEFAULT_ITERATIONS,
        analyse_every=DEFAULT_ANALYSE_EVERY,
        run_id="",
    )
    assert config.monitor.interval == 3
    assert config.monitor.iterations == 1440
    assert config.monitor.analyse_every == 300
    # Bash-parity defaults: cleanup on, 1-day retention, zip mode.
    assert config.monitor.cleanup_enabled is True
    assert config.monitor.cleanup_days == 1
    assert config.monitor.cleanup_mode == "zip"


def test_from_dict_parses_cleanup_fields() -> None:
    config = from_dict(
        {
            "monitor": {
                "cleanup_enabled": False,
                "cleanup_days": 7,
                "cleanup_mode": "delete",
            },
        }
    )
    assert config.monitor.cleanup_enabled is False
    assert config.monitor.cleanup_days == 7
    assert config.monitor.cleanup_mode == "delete"


def test_cleanup_days_rejects_negative() -> None:
    from paperbark.config import ConfigError

    with pytest.raises(ConfigError, match="cleanup_days must be >= 0"):
        from_dict({"monitor": {"cleanup_days": -1}})


def test_cleanup_mode_rejects_unknown_value() -> None:
    from paperbark.config import ConfigError

    with pytest.raises(ConfigError, match="cleanup_mode must be one of"):
        from_dict({"monitor": {"cleanup_mode": "shred"}})


def test_from_dict_parses_monitor_section() -> None:
    config = from_dict(
        {
            "monitor": {
                "interval": 5,
                "iterations": 720,
                "analyse_every": "30s",
                "run_id": "incident-pr349",
            },
        }
    )
    assert config.monitor == MonitorConfig(
        interval=5,
        iterations=720,
        analyse_every=30,
        run_id="incident-pr349",
    )


def test_monitor_interval_accepts_duration_string() -> None:
    config = from_dict({"monitor": {"interval": "5m"}})
    assert config.monitor.interval == 300


def test_monitor_analyse_every_zero_disables_snapshots() -> None:
    # 0 is the documented sentinel for "no snapshot analysis"; must round-trip.
    config = from_dict({"monitor": {"analyse_every": 0}})
    assert config.monitor.analyse_every == 0


def test_monitor_analyse_every_rejects_negative() -> None:
    # Pins ``parse_duration``'s int-side guard against accidental removal —
    # negative ``analyse_every`` would otherwise be coerced to "snapshots
    # disabled" silently, which is the opposite of helpful.
    with pytest.raises(ConfigError, match=r"\[monitor\]\.analyse_every"):
        from_dict({"monitor": {"analyse_every": -5}})


@pytest.mark.parametrize("bad", [0, -1, "0", "0s", -5])
def test_monitor_interval_must_be_positive(bad: object) -> None:
    with pytest.raises(ConfigError, match=r"\[monitor\]\.interval"):
        from_dict({"monitor": {"interval": bad}})


def test_monitor_iterations_rejects_negative() -> None:
    with pytest.raises(ConfigError, match=r"\[monitor\]\.iterations must be >= 0"):
        from_dict({"monitor": {"iterations": -1}})


def test_monitor_run_id_rejects_path_traversal() -> None:
    for bad in ("../escape", ".hidden", "-leading-dash", "with/slash", "with space"):
        with pytest.raises(ConfigError, match=r"\[monitor\]\.run_id"):
            from_dict({"monitor": {"run_id": bad}})


def test_monitor_section_must_be_table() -> None:
    with pytest.raises(ConfigError, match=r"\[monitor\] must be a table"):
        from_dict({"monitor": [1, 2, 3]})


# --- analyse ---------------------------------------------------------------


def test_analyse_defaults_match_argparse_surface() -> None:
    config = Config.defaults()
    assert config.analyse == AnalyseConfig(
        run="latest",
        app="",
        keywords=(),
        regexes=(),
        out="",
        stdout=False,
    )


def test_from_dict_parses_analyse_section() -> None:
    config = from_dict(
        {
            "analyse": {
                "run": "all",
                "app": "web,worker",
                "keywords": ["panic"],
                "regexes": [r"err\d+"],
                "out": "reports/today",
                "stdout": True,
            },
        }
    )
    assert config.analyse == AnalyseConfig(
        run="all",
        app="web,worker",
        keywords=("panic",),
        regexes=(r"err\d+",),
        out="reports/today",
        stdout=True,
    )


@pytest.mark.parametrize("field", ["run", "app", "out"])
def test_analyse_string_field_must_be_string(field: str) -> None:
    with pytest.raises(ConfigError, match=rf"\[analyse\]\.{field}"):
        from_dict({"analyse": {field: 42}})


def test_analyse_stdout_must_be_bool() -> None:
    with pytest.raises(ConfigError, match=r"\[analyse\]\.stdout must be a boolean"):
        from_dict({"analyse": {"stdout": "yes"}})


def test_analyse_keywords_must_be_strings() -> None:
    with pytest.raises(ConfigError, match=r"\[analyse\]\.keywords"):
        from_dict({"analyse": {"keywords": ["ok", 7]}})


def test_analyse_section_must_be_table() -> None:
    with pytest.raises(ConfigError, match=r"\[analyse\] must be a table"):
        from_dict({"analyse": [1, 2, 3]})


# --- search ----------------------------------------------------------------


def test_search_defaults_match_argparse_surface() -> None:
    config = Config.defaults()
    assert config.search == SearchConfig(
        run="latest",
        app="",
        keywords=(),
        regexes=(),
        case_sensitive=False,
        max=0,
    )


def test_from_dict_parses_search_section() -> None:
    config = from_dict(
        {
            "search": {
                "run": "20260503",
                "app": "web",
                "keywords": ["panic"],
                "regexes": [r"5\d\d"],
                "case_sensitive": True,
                "max": 50,
            },
        }
    )
    assert config.search == SearchConfig(
        run="20260503",
        app="web",
        keywords=("panic",),
        regexes=(r"5\d\d",),
        case_sensitive=True,
        max=50,
    )


def test_search_max_rejects_negative() -> None:
    with pytest.raises(ConfigError, match=r"\[search\]\.max must be >= 0"):
        from_dict({"search": {"max": -1}})


@pytest.mark.parametrize(
    "section, field",
    [("monitor", "iterations"), ("search", "max")],
)
def test_int_fields_reject_bool(section: str, field: str) -> None:
    # bool is an int subclass; e.g. `max = true` would otherwise silently cap at 1.
    with pytest.raises(ConfigError, match=rf"\[{section}\]\.{field} must be an integer"):
        from_dict({section: {field: True}})


def test_search_case_sensitive_must_be_bool() -> None:
    with pytest.raises(ConfigError, match=r"\[search\]\.case_sensitive must be a boolean"):
        from_dict({"search": {"case_sensitive": "no"}})


def test_search_section_must_be_table() -> None:
    with pytest.raises(ConfigError, match=r"\[search\] must be a table"):
        from_dict({"search": [1, 2]})


def test_autoupdate_defaults() -> None:
    config = Config.defaults()
    assert config.autoupdate.enabled is True
    assert config.autoupdate.mode == "prompt"
    assert config.autoupdate.check_interval_hours == 24


def test_autoupdate_round_trips() -> None:
    config = from_dict(
        {
            "autoupdate": {
                "enabled": False,
                "mode": "notify",
                "check_interval_hours": 6,
            }
        }
    )
    assert config.autoupdate.enabled is False
    assert config.autoupdate.mode == "notify"
    assert config.autoupdate.check_interval_hours == 6


def test_autoupdate_invalid_mode() -> None:
    with pytest.raises(ConfigError, match=r"\[autoupdate\]\.mode must be one of"):
        from_dict({"autoupdate": {"mode": "shouty"}})


def test_autoupdate_negative_interval_rejected() -> None:
    with pytest.raises(ConfigError, match=r"\[autoupdate\]\.check_interval_hours must be >= 0"):
        from_dict({"autoupdate": {"check_interval_hours": -1}})


def test_autoupdate_section_must_be_table() -> None:
    with pytest.raises(ConfigError, match=r"\[autoupdate\] must be a table"):
        from_dict({"autoupdate": [1, 2]})
