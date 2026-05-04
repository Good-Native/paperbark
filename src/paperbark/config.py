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

    [monitor]
    interval = 3              # seconds between iterations (or "30s", "5m")
    iterations = 1440         # 0 = forever
    analyse_every = "5m"      # 0 = disabled
    run_id = ""               # empty = auto-generated <adjective>-<colour> slug

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

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paperbark.duration import parse_duration

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

# Defaults mirror reference/logs.sh so the Python port behaves identically out
# of the box: 3-second cadence, 1440 iterations (~72 minutes), snapshot
# analysis every 5 minutes, auto-generated run slug.
DEFAULT_INTERVAL = 3
DEFAULT_ITERATIONS = 1440
DEFAULT_ANALYSE_EVERY = 300

# `run_id` is interpolated into a filesystem path; the same character class as
# the bash dispatcher so a hostile or careless value can't escape the
# `logs/YYYYMMDD/HHMM_<slug>_<settings>/` layout.
RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
RUN_ID_HELP = (
    "run_id may only contain letters, numbers, dot, underscore, and hyphen,"
    " and may not start with '.' or '-'"
)
_RUN_ID_RE = re.compile(RUN_ID_PATTERN)


def is_valid_run_id(value: str) -> bool:
    """Return ``True`` if ``value`` is empty or matches :data:`RUN_ID_PATTERN`.

    The CLI override path and the TOML loader both call this so a hostile
    value supplied via ``--run-id`` can't slip past the validation that
    :func:`_parse_monitor` enforces on the TOML side.
    """
    if not value:
        return True
    return bool(_RUN_ID_RE.match(value))


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
        """Return ``True`` if the named probe is enabled.

        Only names listed in :data:`PROBE_NAMES` count — passing an unrelated
        attribute (``keywords``, ``regexes``, …) returns ``False`` rather than
        leaking the truthiness of the underlying value.
        """
        if name not in PROBE_NAMES:
            return False
        return bool(getattr(self, name))


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One captured source plus its type-specific options."""

    name: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    """Cadence, scope, and identity settings for ``paperbark monitor``.

    All values are stored as plain ints (seconds for time fields) so the
    dispatcher and animator never re-parse user input. ``iterations = 0`` runs
    forever; ``analyse_every = 0`` disables snapshot analysis; ``run_id = ""``
    triggers an auto-generated ``<adjective>-<colour>`` slug at run time.
    """

    interval: int = DEFAULT_INTERVAL
    iterations: int = DEFAULT_ITERATIONS
    analyse_every: int = DEFAULT_ANALYSE_EVERY
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class Config:
    """Parsed paperbark configuration."""

    root: Path = field(default_factory=lambda: Path(DEFAULT_ROOT))
    sources: tuple[SourceConfig, ...] = ()
    probes: ProbesConfig = field(default_factory=ProbesConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)

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
        # ``is_file()`` instead of ``exists()`` so a directory named
        # ``paperbark.toml`` (an easy mistake) doesn't win discovery and mask
        # a valid home config sitting behind it.
        if candidate.is_file():
            return candidate
    return None


def from_dict(raw: Mapping[str, Any]) -> Config:
    """Build a :class:`Config` from a raw parsed-TOML mapping.

    ``tomllib.load`` always returns a dict, so this top-level guard mainly
    catches programmatic callers that hand in a non-mapping (e.g. a list or
    a scalar). Without it the first ``raw.get`` would raise ``AttributeError``
    rather than the project's typed :class:`ConfigError`.
    """
    if not isinstance(raw, Mapping):
        raise ConfigError(f"config root must be a table, got {type(raw).__name__}")
    paperbark = _expect_mapping(raw.get("paperbark"), "paperbark")
    root_raw = paperbark.get("root", DEFAULT_ROOT)
    if not isinstance(root_raw, str):
        raise ConfigError(f"[paperbark].root must be a string, got {type(root_raw).__name__}")
    return Config(
        root=Path(root_raw),
        sources=_parse_sources(raw.get("sources")),
        probes=_parse_probes(raw.get("probes")),
        monitor=_parse_monitor(raw.get("monitor")),
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


def _parse_monitor(raw: Any) -> MonitorConfig:
    table = _expect_mapping(raw, "monitor")
    interval = _parse_duration_field(
        table.get("interval", DEFAULT_INTERVAL),
        "[monitor].interval",
        require_positive=True,
    )
    iterations_raw = table.get("iterations", DEFAULT_ITERATIONS)
    if isinstance(iterations_raw, bool) or not isinstance(iterations_raw, int):
        # bool is an int subclass; reject it explicitly so `iterations = true`
        # fails closed instead of being read as `1`.
        raise ConfigError(
            f"[monitor].iterations must be an integer, got {type(iterations_raw).__name__}"
        )
    if iterations_raw < 0:
        raise ConfigError("[monitor].iterations must be >= 0")
    analyse_every = _parse_duration_field(
        table.get("analyse_every", DEFAULT_ANALYSE_EVERY),
        "[monitor].analyse_every",
        require_positive=False,
        require_non_negative=True,
    )
    run_id_raw = table.get("run_id", "")
    if not isinstance(run_id_raw, str):
        raise ConfigError(f"[monitor].run_id must be a string, got {type(run_id_raw).__name__}")
    if not is_valid_run_id(run_id_raw):
        raise ConfigError(f"[monitor].{RUN_ID_HELP}")
    return MonitorConfig(
        interval=interval,
        iterations=iterations_raw,
        analyse_every=analyse_every,
        run_id=run_id_raw,
    )


def _parse_duration_field(
    value: Any,
    label: str,
    *,
    require_positive: bool,
    require_non_negative: bool = False,
) -> int:
    """Validate a TOML duration field, accepting int seconds or shorthand strings.

    ``require_positive`` rejects ``0``; ``require_non_negative`` only rejects
    negative integers (negatives slip past :func:`parse_duration` itself when
    they are passed as ints, since the parser short-circuits on the int form).
    """
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ConfigError(
            f"{label} must be an integer or duration string, got {type(value).__name__}"
        )
    try:
        seconds = parse_duration(value)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{label}: {exc}") from exc
    if require_positive and seconds <= 0:
        raise ConfigError(f"{label} must be > 0")
    if require_non_negative and seconds < 0:
        raise ConfigError(f"{label} must be >= 0")
    return seconds


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
