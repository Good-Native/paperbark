"""Tests for SeverityProbe."""

from __future__ import annotations

from paperbark.probes import SeverityProbe
from paperbark.probes._record import parse_line


def _record(level: str, message: str = "x") -> object:
    return parse_line(f'2026-05-03T02:00:01Z {{"level":"{level}","msg":"{message}"}}\n')


def test_known_levels_reported_in_canonical_order() -> None:
    probe = SeverityProbe()
    probe.feed(_record("error"))  # type: ignore[arg-type]
    probe.feed(_record("info"))  # type: ignore[arg-type]
    probe.feed(_record("warn"))  # type: ignore[arg-type]
    report = probe.report()
    labels = [f["label"] for f in report["findings"]]
    assert labels == ["info", "warn", "error"]


def test_levels_with_zero_count_are_omitted() -> None:
    probe = SeverityProbe()
    probe.feed(_record("error"))  # type: ignore[arg-type]
    labels = [f["label"] for f in probe.report()["findings"]]
    assert labels == ["error"]


def test_unknown_level_rolls_up_under_unknown_label() -> None:
    probe = SeverityProbe()
    probe.feed(_record("trace"))  # type: ignore[arg-type]
    probe.feed(_record("notice"))  # type: ignore[arg-type]
    findings = probe.report()["findings"]
    assert len(findings) == 1
    assert findings[0]["label"] == "unknown-level"
    assert findings[0]["count"] == 2


def test_empty_level_records_are_ignored() -> None:
    probe = SeverityProbe()
    probe.feed(parse_line("2026-05-03T02:00:01Z plain text no json\n"))
    assert probe.report()["findings"] == []
    assert probe.report()["name"] == "Severity"
