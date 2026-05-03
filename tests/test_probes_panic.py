"""Tests for PanicProbe."""

from __future__ import annotations

from paperbark.probes import PanicProbe
from paperbark.probes._record import parse_line


def test_panic_groups_by_first_line_of_cause() -> None:
    probe = PanicProbe()
    probe.feed(parse_line("2026-05-03T02:00:01Z panic: runtime error: nil deref\n"))
    probe.feed(parse_line("2026-05-03T02:00:02Z panic: runtime error: nil deref\n"))
    probe.feed(parse_line("2026-05-03T02:00:03Z panic: out of bounds\n"))
    findings = {f["label"]: f["count"] for f in probe.report()["findings"]}
    assert findings == {
        "panic: runtime error: nil deref": 2,
        "panic: out of bounds": 1,
    }


def test_fatal_lines_bucket_under_fatal_prefix() -> None:
    probe = PanicProbe()
    probe.feed(parse_line("2026-05-03T02:00:01Z fatal error: cannot bind socket\n"))
    findings = probe.report()["findings"]
    assert findings[0]["label"].startswith("fatal: ")


def test_panic_findings_capped_at_top_n() -> None:
    probe = PanicProbe()
    for i in range(15):
        probe.feed(parse_line(f"2026-05-03T02:00:01Z panic: cause-{i:02d}\n"))
    findings = probe.report()["findings"]
    assert len(findings) == 10


def test_no_panic_lines_returns_empty_findings() -> None:
    probe = PanicProbe()
    probe.feed(parse_line("2026-05-03T02:00:01Z just a normal line\n"))
    assert probe.report()["findings"] == []
