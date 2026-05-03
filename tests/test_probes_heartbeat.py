"""Tests for HeartbeatProbe."""

from __future__ import annotations

from paperbark.probes import HeartbeatProbe
from paperbark.probes._record import parse_line


def _info_line(timestamp: str) -> object:
    return parse_line(f'{timestamp}Z {{"level":"info","msg":"tick"}}\n')


def _warn_line(timestamp: str) -> object:
    return parse_line(f'{timestamp}Z {{"level":"warn","msg":"alert"}}\n')


def test_no_traffic_returns_note() -> None:
    probe = HeartbeatProbe()
    report = probe.report()
    assert report["findings"] == []
    assert report["note"] == "no timestamped traffic"


def test_unbroken_info_traffic_reports_no_gaps() -> None:
    probe = HeartbeatProbe()
    for minute in range(5):
        probe.feed(_info_line(f"2026-05-03T02:0{minute}:30"))  # type: ignore[arg-type]
    report = probe.report()
    assert report["gap_minutes"] == []


def test_zero_info_minute_with_other_traffic_marked_as_gap() -> None:
    probe = HeartbeatProbe()
    # Minutes 00 and 04 carry info; 01-03 carry only warn.
    probe.feed(_info_line("2026-05-03T02:00:30"))  # type: ignore[arg-type]
    for minute in (1, 2, 3):
        probe.feed(_warn_line(f"2026-05-03T02:0{minute}:30"))  # type: ignore[arg-type]
    probe.feed(_info_line("2026-05-03T02:04:30"))  # type: ignore[arg-type]
    gap_minutes = {g["minute"] for g in probe.report()["gap_minutes"]}
    assert gap_minutes == {
        "2026-05-03T02:01",
        "2026-05-03T02:02",
        "2026-05-03T02:03",
    }


def test_entirely_missing_minute_between_observed_minutes_is_a_gap() -> None:
    probe = HeartbeatProbe()
    # Minute 00 and 02 carry info; minute 01 has no records at all.
    probe.feed(_info_line("2026-05-03T02:00:30"))  # type: ignore[arg-type]
    probe.feed(_info_line("2026-05-03T02:02:30"))  # type: ignore[arg-type]
    # With only two observed minutes the slice [1:-1] is empty, so the
    # only way 02:01 surfaces is via the inter-minute fill.
    gap_minutes = {g["minute"] for g in probe.report()["gap_minutes"]}
    assert "2026-05-03T02:01" in gap_minutes
