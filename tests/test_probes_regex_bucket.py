"""Tests for RegexBucketProbe."""

from __future__ import annotations

from paperbark.probes import RegexBucketProbe, default_probes
from paperbark.probes._record import parse_line


def test_pattern_matches_bucket_per_label() -> None:
    probe = RegexBucketProbe(
        "Process health",
        [
            ("starting", r"starting machine"),
            ("oom", r"out of memory"),
        ],
    )
    probe.feed(parse_line("2026-05-03T02:00:01Z starting machine 1234\n"))
    probe.feed(parse_line("2026-05-03T02:00:02Z out of memory: killed\n"))
    probe.feed(parse_line("2026-05-03T02:00:03Z starting machine 5678\n"))
    findings = {f["label"]: f["count"] for f in probe.report()["findings"]}
    assert findings == {"starting": 2, "oom": 1}


def test_findings_sorted_by_descending_count() -> None:
    probe = RegexBucketProbe(
        "x",
        [
            ("rare", r"rare"),
            ("common", r"common"),
        ],
    )
    for _ in range(3):
        probe.feed(parse_line("2026-05-03T02:00:01Z common\n"))
    probe.feed(parse_line("2026-05-03T02:00:02Z rare\n"))
    labels = [f["label"] for f in probe.report()["findings"]]
    assert labels == ["common", "rare"]


def test_one_line_can_match_multiple_labels() -> None:
    probe = RegexBucketProbe(
        "x",
        [
            ("foo", r"foo"),
            ("bar", r"bar"),
        ],
    )
    probe.feed(parse_line("2026-05-03T02:00:01Z foo and bar together\n"))
    findings = {f["label"]: f["count"] for f in probe.report()["findings"]}
    assert findings == {"foo": 1, "bar": 1}


def test_default_probes_includes_expected_set() -> None:
    names = [p.name for p in default_probes()]
    assert names == [
        "Severity",
        "Panics & fatals",
        "HTTP status",
        "Latency",
        "Heartbeat",
        "Process health",
        "Autoscaler",
        "Database / external",
        "Sentry",
    ]


def test_default_probes_appends_adhoc_when_terms_supplied() -> None:
    probes = default_probes(extra_keywords=["banana"], extra_regexes=[r"err\d+"])
    assert probes[-1].name == "Ad-hoc keywords"
    probes[-1].feed(parse_line("2026-05-03T02:00:01Z plain banana here\n"))
    probes[-1].feed(parse_line("2026-05-03T02:00:02Z err42 occurred\n"))
    findings = {f["label"]: f["count"] for f in probes[-1].report()["findings"]}
    assert findings == {"keyword:banana": 1, "regex:err\\d+": 1}
