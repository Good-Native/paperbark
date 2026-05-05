"""Tests for HTTPStatusProbe."""

from __future__ import annotations

from paperbark.probes import HTTPStatusProbe
from paperbark.probes._record import parse_line


def test_class_buckets_track_each_response_class() -> None:
    probe = HTTPStatusProbe()
    probe.feed(parse_line('2026-05-03T02:00:01Z {"status":200,"msg":"ok"}\n'))
    probe.feed(parse_line('2026-05-03T02:00:02Z {"status":404,"msg":"nope"}\n'))
    probe.feed(parse_line('2026-05-03T02:00:03Z {"status":503,"msg":"down"}\n'))
    findings = {f["label"]: f["count"] for f in probe.report()["findings"]}
    # Class buckets always present; explicit code buckets only for triage codes.
    assert findings["2xx"] == 1
    assert findings["4xx"] == 1
    assert findings["5xx"] == 1
    assert findings["503"] == 1
    assert "404" not in findings


def test_status_extracted_from_access_log() -> None:
    probe = HTTPStatusProbe()
    probe.feed(parse_line('2026-05-03T02:00:01Z 1.2.3.4 - - "GET /x HTTP/1.1" 429 0\n'))
    findings = {f["label"]: f["count"] for f in probe.report()["findings"]}
    assert findings == {"4xx": 1, "429": 1}
