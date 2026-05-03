"""Tests for the Bucket accumulator."""

from __future__ import annotations

from paperbark.probes._bucket import Bucket


def test_empty_bucket_to_finding_zero_count() -> None:
    finding = Bucket().to_finding("nothing")
    assert finding.label == "nothing"
    assert finding.count == 0
    assert finding.first_seen == ""
    assert finding.last_seen == ""
    assert finding.peak == ""
    assert finding.peak_count == 0
    assert finding.samples == ()


def test_bucket_tracks_first_last_and_peak() -> None:
    bucket = Bucket()
    # Two events in the same minute (peak), one in another.
    bucket.add("2026-05-03T02:00:01", "first")
    bucket.add("2026-05-03T02:00:30", "same minute")
    bucket.add("2026-05-03T03:05:00", "later")
    finding = bucket.to_finding("hits")
    assert finding.count == 3
    assert finding.first_seen == "2026-05-03T02:00:01"
    assert finding.last_seen == "2026-05-03T03:05:00"
    assert finding.peak == "2026-05-03T02:00"
    assert finding.peak_count == 2


def test_bucket_keeps_at_most_three_samples() -> None:
    bucket = Bucket()
    for i in range(5):
        bucket.add("2026-05-03T02:00:01", f"sample {i}")
    finding = bucket.to_finding("x")
    assert len(finding.samples) == 3
    assert finding.samples[0] == "sample 0"


def test_bucket_trims_long_samples() -> None:
    bucket = Bucket()
    bucket.add("2026-05-03T02:00:01", "x" * 500)
    sample = bucket.to_finding("x").samples[0]
    assert len(sample) == 240
    assert sample.endswith("...")


def test_bucket_skips_empty_sample_after_trimming() -> None:
    bucket = Bucket()
    bucket.add("2026-05-03T02:00:01", "   ")
    assert bucket.to_finding("x").samples == ()


def test_bucket_handles_missing_timestamp() -> None:
    bucket = Bucket()
    bucket.add("", "ts-less")
    finding = bucket.to_finding("x")
    assert finding.count == 1
    assert finding.first_seen == ""
    assert finding.peak == ""
