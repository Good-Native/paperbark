"""Tests for LatencyProbe."""

from __future__ import annotations

from paperbark.probes import LatencyProbe
from paperbark.probes._record import parse_line
from paperbark.probes.latency import _percentile


def test_no_durations_seen_reports_note() -> None:
    probe = LatencyProbe()
    probe.feed(parse_line("2026-05-03T02:00:01Z plain text\n"))
    report = probe.report()
    assert report["findings"] == []
    assert report["note"] == "no duration fields seen"


def test_percentiles_use_linear_interpolation() -> None:
    # Banker's-rounding regression: p50 of [100, 300] must be 200, not 100.
    assert _percentile([100.0, 300.0], 50) == 200.0
    assert _percentile([100.0], 50) == 100.0
    assert _percentile([], 50) == 0.0


def test_percentiles_at_extremes() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(values, 0) == 10.0
    assert _percentile(values, 100) == 50.0


def test_report_summarises_durations() -> None:
    probe = LatencyProbe()
    for ms in (10.0, 20.0, 30.0, 40.0, 50.0):
        probe.feed(parse_line(f'2026-05-03T02:00:01Z {{"duration_ms":{ms},"msg":"x"}}\n'))
    report = probe.report()
    assert report["samples"] == 5
    assert report["max_ms"] == 50.0
    assert report["mean_ms"] == 30.0
    assert report["p50_ms"] == 30.0


def test_durations_outside_sane_range_dropped() -> None:
    probe = LatencyProbe()
    probe.feed(parse_line('2026-05-03T02:00:01Z {"duration_ms":-5,"msg":"x"}\n'))
    probe.feed(parse_line('2026-05-03T02:00:01Z {"duration_ms":99999999,"msg":"x"}\n'))
    probe.feed(parse_line('2026-05-03T02:00:01Z {"duration_ms":50,"msg":"x"}\n'))
    assert probe.report()["samples"] == 1
