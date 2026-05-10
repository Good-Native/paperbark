"""Project-manifest detection for ``paperbark init``.

Inspects the supplied directory for ``fly.toml`` and
``wrangler.{toml,jsonc}`` and returns the source entries that
``paperbark init`` should pre-fill into the generated ``paperbark.toml``.
A user who has already configured their app with ``flyctl`` or
``wrangler`` shouldn't have to retype the app/worker name into a
second config file — paperbark reads it back out of the existing
manifest.

Detection is one-shot at ``init`` time, never at ``monitor`` time:
the project rule in ``CLAUDE.md`` keeps the TOML config as the
single source of truth for runtime defaults. Pre-filling the file
on ``init`` doesn't break that — the file is still authoritative
once written.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DetectedSource:
    """One source entry inferred from a project manifest.

    ``name`` is what the user will see in run-dir paths and probe
    output; ``type`` matches the ``[[sources]].type`` value the
    config loader expects. Exactly one of ``app`` (flyctl) or
    ``worker`` (wrangler) is set; ``account_id`` is wrangler-only.
    """

    name: str
    type: str
    app: str | None = None
    worker: str | None = None
    account_id: str | None = None


# JSONC supports ``//`` and ``/* */`` comments and trailing commas.
# This regex matches a string literal OR a comment so we can preserve
# strings (which may legitimately contain ``//`` or ``/*``) while
# stripping comments. Without the string alternative, a value like
# ``"https://..."`` would have its ``//`` and everything after it
# erased — silently corrupting the parse.
_JSONC_TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
    re.DOTALL,
)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments and trailing commas, leaving valid JSON."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token.startswith('"') else ""

    stripped = _JSONC_TOKEN.sub(_replace, text)
    return _TRAILING_COMMA.sub(r"\1", stripped)


def _warn(message: str) -> None:
    """Detection failures are advisory — keep ``init`` on the happy path."""
    sys.stderr.write(f"paperbark detect: {message}\n")


def _detect_fly(cwd: Path) -> DetectedSource | None:
    fly = cwd / "fly.toml"
    if not fly.is_file():
        return None
    try:
        data = tomllib.loads(fly.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _warn(f"could not parse fly.toml: {exc}")
        return None
    # Modern fly.toml uses ``app``; very old configs used ``app_name``.
    # Try the modern key first; fall through so a stray ``app_name``
    # left over from a 2022-era config still gets picked up.
    app = data.get("app") or data.get("app_name")
    if not isinstance(app, str) or not app:
        _warn("fly.toml has no top-level 'app'")
        return None
    return DetectedSource(name="fly", type="flyctl", app=app)


def _wrangler_payload(cwd: Path) -> Mapping[str, Any] | None:
    """Return the parsed top-level table from ``wrangler.toml`` or
    ``wrangler.jsonc``, preferring TOML when both exist (matches
    wrangler's own resolution order in 4.x)."""
    toml = cwd / "wrangler.toml"
    if toml.is_file():
        try:
            return tomllib.loads(toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            _warn(f"could not parse wrangler.toml: {exc}")
            return None
    jsonc = cwd / "wrangler.jsonc"
    if jsonc.is_file():
        try:
            stripped = _strip_jsonc(jsonc.read_text(encoding="utf-8"))
            parsed = json.loads(stripped)
        except (OSError, json.JSONDecodeError) as exc:
            _warn(f"could not parse wrangler.jsonc: {exc}")
            return None
        if not isinstance(parsed, dict):
            _warn("wrangler.jsonc top level is not an object")
            return None
        return parsed
    # Plain ``wrangler.json`` is rare but supported by wrangler 4.x.
    plain = cwd / "wrangler.json"
    if plain.is_file():
        try:
            parsed = json.loads(plain.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _warn(f"could not parse wrangler.json: {exc}")
            return None
        if not isinstance(parsed, dict):
            _warn("wrangler.json top level is not an object")
            return None
        return parsed
    return None


def _detect_wrangler(cwd: Path) -> DetectedSource | None:
    payload = _wrangler_payload(cwd)
    if payload is None:
        return None
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        _warn("wrangler config has no top-level 'name'")
        return None
    account_id_raw = payload.get("account_id")
    account_id = account_id_raw if isinstance(account_id_raw, str) and account_id_raw else None
    return DetectedSource(
        name="wrangler",
        type="wrangler",
        worker=name,
        account_id=account_id,
    )


def detect(cwd: Path) -> list[DetectedSource]:
    """Inspect ``cwd`` and return any sources we can pre-populate.

    Order is deterministic (fly first, wrangler second) so the
    generated ``paperbark.toml`` is byte-stable for the same input.
    Source ``name`` values are unique by construction (``fly`` vs
    ``wrangler``), so the config loader's duplicate-name check
    won't trip even when both manifests are present.
    """
    detected: list[DetectedSource] = []
    fly = _detect_fly(cwd)
    if fly is not None:
        detected.append(fly)
    wrangler = _detect_wrangler(cwd)
    if wrangler is not None:
        detected.append(wrangler)
    return detected
