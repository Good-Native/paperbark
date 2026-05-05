"""Tests for ``[probes]`` config wiring through :func:`default_probes`.

These tests pin the contract that ``CONFIG.md`` advertises:

- Toggles drop probes from the returned set;
- ``[probes].keywords`` / ``[probes].regexes`` fold into the ``Ad-hoc
  keywords`` bucket alongside any ``extra_keywords`` / ``extra_regexes``
  the CLI passes;
- ``[probes.patterns]`` overrides replace the built-in regex set for the
  named probe (overrides do not extend — the built-ins are dropped when
  an override is present).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paperbark.analyse import run as run_analyse
from paperbark.config import PatternOverride, ProbesConfig
from paperbark.probes import Probe, RegexBucketProbe, default_probes


def _names(probes: list[Probe]) -> list[str]:
    return [p.name for p in probes]


def _adhoc_labels(probes: list[Probe]) -> list[str]:
    """Return the labels of the trailing ``Ad-hoc keywords`` regex bucket."""
    adhoc = next(p for p in probes if p.name == "Ad-hoc keywords")
    assert isinstance(adhoc, RegexBucketProbe)
    return [label for label, _ in adhoc._compiled]


def _bucket_labels(probes: list[Probe], name: str) -> list[str]:
    """Return the configured labels for a named regex-bucket probe."""
    bucket = next(p for p in probes if p.name == name)
    assert isinstance(bucket, RegexBucketProbe)
    return [label for label, _ in bucket._compiled]


def _build_run(root: Path) -> Path:
    """Seed a one-app run with traffic that exercises every default probe."""
    lines = [
        '{"time":"2026-05-03T14:30:01Z","level":"info","msg":"hello"}',
        '{"time":"2026-05-03T14:30:02Z","level":"info","msg":"world"}',
        '{"time":"2026-05-03T14:30:03Z","level":"error","msg":"bang"}',
        "panic: db down",
        '{"time":"2026-05-03T14:30:04Z","level":"info","msg":"deadlock detected"}',
        '{"time":"2026-05-03T14:30:05Z","level":"info","msg":"req","status":500,"duration_ms":120}',
    ]
    run_dir = root / "20260503" / "1430_demo"
    raw = run_dir / "demo-app" / "raw" / "sample.log"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir


def test_default_probes_returns_full_set_with_no_config() -> None:
    names = _names(default_probes())
    assert names == [
        "Severity",
        "Panics & fatals",
        "HTTP status",
        "Latency",
        "Heartbeat",
        "Process health",
        "Autoscaler",
        "External errors and timeouts",
        "Sentry",
    ]


def test_default_probes_drops_disabled_probes() -> None:
    cfg = ProbesConfig(severity=False, panics=False, sentry=False)
    names = _names(default_probes(config=cfg))
    assert "Severity" not in names
    assert "Panics & fatals" not in names
    assert "Sentry" not in names
    # Untouched defaults remain.
    assert "HTTP status" in names
    assert "External errors and timeouts" in names


def test_default_probes_folds_config_keywords_into_adhoc_bucket() -> None:
    cfg = ProbesConfig(keywords=("circuit-open",), regexes=(r"err\d+",))
    assert _adhoc_labels(default_probes(config=cfg)) == [
        "keyword:circuit-open",
        "regex:err\\d+",
    ]


def test_default_probes_extra_args_combine_with_config_terms() -> None:
    """CLI extras should append to TOML-supplied ``[probes].keywords``."""
    cfg = ProbesConfig(keywords=("from-config",))
    assert _adhoc_labels(default_probes(["from-cli"], config=cfg)) == [
        "keyword:from-config",
        "keyword:from-cli",
    ]


def test_pattern_override_replaces_builtin_regex_set() -> None:
    cfg = ProbesConfig(
        pattern_overrides={
            "database": (PatternOverride(label="pg-deadlock", pattern="deadlock detected"),),
        }
    )
    # Override replaces, does not extend: built-ins like ``connection refused``
    # disappear when ``[probes.patterns].database`` is supplied.
    assert _bucket_labels(default_probes(config=cfg), "External errors and timeouts") == [
        "pg-deadlock"
    ]


def test_pattern_override_unaffected_probe_keeps_builtin_set() -> None:
    cfg = ProbesConfig(
        pattern_overrides={
            "autoscaler": (PatternOverride(label="k8s-evict", pattern="Evicting pod"),),
        }
    )
    assert "connection refused" in _bucket_labels(
        default_probes(config=cfg), "External errors and timeouts"
    )


def test_disabled_probe_with_override_is_still_dropped() -> None:
    """A toggle of ``false`` wins over a ``[probes.patterns]`` entry."""
    cfg = ProbesConfig(
        autoscaler=False,
        pattern_overrides={
            "autoscaler": (PatternOverride(label="anything", pattern="x"),),
        },
    )
    names = _names(default_probes(config=cfg))
    assert "Autoscaler" not in names


# --- end-to-end via paperbark analyse --------------------------------------


def _ns(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "run": "latest",
        "root": "logs",
        "app": "",
        "keyword": [],
        "regex": [],
        "out": None,
        "stdout": False,
        "probes": ProbesConfig(),
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_analyse_omits_disabled_probes_in_report(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)
    cfg = ProbesConfig(severity=False, panics=False)

    rc = run_analyse(_ns(root=str(root), probes=cfg))

    assert rc == 0
    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    names = [p["name"] for p in payload["apps"][0]["probes"]]
    assert "Severity" not in names
    assert "Panics & fatals" not in names
    assert "HTTP status" in names


def test_analyse_pattern_override_drives_findings(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)
    cfg = ProbesConfig(
        pattern_overrides={
            "database": (PatternOverride(label="pg-deadlock", pattern="deadlock detected"),),
        }
    )

    rc = run_analyse(_ns(root=str(root), probes=cfg))

    assert rc == 0
    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    db = next(
        p for p in payload["apps"][0]["probes"] if p["name"] == "External errors and timeouts"
    )
    labels = [f["label"] for f in db["findings"]]
    # ``pg-deadlock`` matches the seeded line; the built-in labels would
    # have produced no matches against this fixture.
    assert labels == ["pg-deadlock"]


def test_analyse_config_keywords_appear_in_adhoc_bucket(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    run_dir = _build_run(root)
    cfg = ProbesConfig(keywords=("world",))

    rc = run_analyse(_ns(root=str(root), probes=cfg))

    assert rc == 0
    payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    adhoc = next(p for p in payload["apps"][0]["probes"] if p["name"] == "Ad-hoc keywords")
    assert any(f["label"] == "keyword:world" and f["count"] == 1 for f in adhoc["findings"])
