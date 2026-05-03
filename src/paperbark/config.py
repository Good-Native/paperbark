"""TOML configuration loader.

Discovers and parses the paperbark config file. Discovery order:

1. Explicit path passed to :func:`load`.
2. ``./paperbark.toml`` (current working directory).
3. ``~/.config/paperbark/config.toml``.

If nothing is found, :func:`load` returns :class:`Config.defaults()`.

The shape this loader produces is the source-of-truth for what the
dispatcher (step 8) and `paperbark init` (step 9) consume; CLI flags
override these values at runtime.

Schema overview::

    [paperbark]
    root = "logs"             # output directory; default "logs"

    [probes]
    severity = true
    panics = true
    http = true
    latency = true
    heartbeat = true
    process_health = true
    autoscaler = true
    database = true
    sentry = true
    keywords = ["panic"]
    regexes = ["err\\d+"]

    [probes.patterns]
    autoscaler = [
        { label = "reconciling", pattern = "reconciling app" },
    ]

    [[sources]]
    name = "main"
    type = "flyctl"
    app = "fly-app-a"
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = "logs"
PROBE_NAMES: tuple[str, ...] = (
    "severity",
    "panics",
    "http",
    "latency",
    "heartbeat",
    "process_health",
    "autoscaler",
    "database",
    "sentry",
)


class ConfigError(ValueError):
    """Raised when the TOML file is structurally valid but semantically wrong."""


@dataclass(frozen=True, slots=True)
class PatternOverride:
    label: str
    pattern: str


@dataclass(frozen=True, slots=True)
class ProbesConfig:
    """Probe toggles, ad-hoc patterns, and pattern overrides."""

    severity: bool = True
    panics: bool = True
    http: bool = True
    latency: bool = True
    heartbeat: bool = True
    process_health: bool = True
    autoscaler: bool = True
    database: bool = True
    sentry: bool = True
    keywords: tuple[str, ...] = ()
    regexes: tuple[str, ...] = ()
    pattern_overrides: dict[str, tuple[PatternOverride, ...]] = field(default_factory=dict)

    def is_enabled(self, name: str) -> bool:
        """Return ``True`` if the named probe is enabled."""
        return bool(getattr(self, name, False))


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One captured source plus its type-specific options."""

    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Config:
    """Parsed paperbark configuration."""

    root: Path = field(default_factory=lambda: Path(DEFAULT_ROOT))
    sources: tuple[SourceConfig, ...] = ()
    probes: ProbesConfig = field(default_factory=ProbesConfig)

    @classmethod
    def defaults(cls) -> Config:
        """Return the default config used when no file is found."""
        return cls()


def load(path: Path | None = None, *, cwd: Path | None = None) -> Config:
    """Load configuration from ``path``, discovering it if not supplied.

    ``cwd`` is injectable for testability; defaults to :func:`Path.cwd`.
    Returns :func:`Config.defaults` when no file is found and ``path``
    is not supplied.
    """
    target = path if path is not None else discover(cwd=cwd)
    if target is None:
        return Config.defaults()
    if not target.exists() or not target.is_file():
        # Catches the directory-as-path case, dangling symlinks, and the rare
        # race where a file is removed between discover() and open().
        raise ConfigError(f"config file not found: {target}")
    try:
        with target.open("rb") as f:
            raw = tomllib.load(f)
    except OSError as exc:
        raise ConfigError(f"unable to read config file {target}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {target}: {exc}") from exc
    return from_dict(raw)


def discover(*, cwd: Path | None = None) -> Path | None:
    """Return the highest-priority config path that exists, or ``None``."""
    base = cwd if cwd is not None else Path.cwd()
    candidates = [
        base / "paperbark.toml",
        Path.home() / ".config" / "paperbark" / "config.toml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def from_dict(raw: Mapping[str, Any]) -> Config:
    """Build a :class:`Config` from a raw parsed-TOML mapping."""
    paperbark = _expect_mapping(raw.get("paperbark"), "paperbark")
    root_raw = paperbark.get("root", DEFAULT_ROOT)
    if not isinstance(root_raw, str):
        raise ConfigError(f"[paperbark].root must be a string, got {type(root_raw).__name__}")
    return Config(
        root=Path(root_raw),
        sources=_parse_sources(raw.get("sources")),
        probes=_parse_probes(raw.get("probes")),
    )


# --- Internals --------------------------------------------------------------


def _expect_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a table, got {type(value).__name__}")
    return value


def _parse_sources(raw: Any) -> tuple[SourceConfig, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("[[sources]] must be an array of tables")
    sources: list[SourceConfig] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ConfigError(f"sources[{index}] must be a table")
        name = entry.get("name")
        type_ = entry.get("type")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"sources[{index}] missing or invalid 'name'")
        if not isinstance(type_, str) or not type_:
            raise ConfigError(f"sources[{index}] ({name}) missing or invalid 'type'")
        if name in seen_names:
            raise ConfigError(f"duplicate source name: {name!r}")
        seen_names.add(name)
        options = {k: v for k, v in entry.items() if k not in ("name", "type")}
        sources.append(SourceConfig(name=name, type=type_, options=options))
    return tuple(sources)


def _parse_probes(raw: Any) -> ProbesConfig:
    table = _expect_mapping(raw, "probes")
    toggles: dict[str, bool] = {}
    for name in PROBE_NAMES:
        if name in table:
            value = table[name]
            if not isinstance(value, bool):
                raise ConfigError(f"[probes].{name} must be a boolean, got {type(value).__name__}")
            toggles[name] = value
    keywords = _parse_string_list(table.get("keywords"), "[probes].keywords")
    regexes = _parse_string_list(table.get("regexes"), "[probes].regexes")
    pattern_overrides = _parse_pattern_overrides(table.get("patterns"))
    return ProbesConfig(
        keywords=keywords,
        regexes=regexes,
        pattern_overrides=pattern_overrides,
        **toggles,
    )


def _parse_string_list(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{label} must be an array of strings")
    items: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            raise ConfigError(f"{label}[{index}] must be a string")
        items.append(item)
    return tuple(items)


def _parse_pattern_overrides(raw: Any) -> dict[str, tuple[PatternOverride, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError("[probes.patterns] must be a table")
    overrides: dict[str, tuple[PatternOverride, ...]] = {}
    for probe_name, entries in raw.items():
        if not isinstance(entries, list):
            raise ConfigError(
                f"[probes.patterns].{probe_name} must be an array of {{label, pattern}} tables"
            )
        parsed: list[PatternOverride] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise ConfigError(
                    f"[probes.patterns].{probe_name}[{index}] must be a {{label, pattern}} table"
                )
            label = entry.get("label")
            pattern = entry.get("pattern")
            if not isinstance(label, str) or not label:
                raise ConfigError(
                    f"[probes.patterns].{probe_name}[{index}] missing or invalid 'label'"
                )
            if not isinstance(pattern, str) or not pattern:
                raise ConfigError(
                    f"[probes.patterns].{probe_name}[{index}] missing or invalid 'pattern'"
                )
            parsed.append(PatternOverride(label=label, pattern=pattern))
        overrides[probe_name] = tuple(parsed)
    return overrides
