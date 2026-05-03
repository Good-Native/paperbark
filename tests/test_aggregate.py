"""Tests for paperbark.aggregate."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from paperbark.aggregate import (
    AggregateState,
    MinuteBucket,
    aggregate,
    cli,
    load_state,
    merge_iteration,
    save_state,
    write_components_csv,
    write_events_csv,
    write_summary,
    write_time_series,
)


def _sample_payload() -> dict[str, object]:
    return {
        "meta": {"total_lines": 100, "failed_to_parse": 5},
        "level_counts": {
            "2026-05-03T02:00:01": {"info": 8, "warn": 2},
            "2026-05-03T02:01:00": {"info": 6, "error": 1},
        },
        "event_counts": {
            "2026-05-03T02:00:01": [
                {"event": "api: served", "count": 7},
                {"event": "api: rejected", "count": 1},
            ],
            "2026-05-03T02:01:00": [{"event": "worker: ran", "count": 3}],
        },
        "component_counts": {
            "2026-05-03T02:00:01": {"api": 8, "worker": 2},
            "2026-05-03T02:01:00": {"worker": 7},
        },
        "warn_error_counts": {"timeouts": 4, "panics": 1},
    }


def test_merge_one_payload_populates_minute_buckets() -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    assert state.total_lines == 100
    assert state.failed_to_parse == 5
    assert state.warn_error_counts == {"timeouts": 4, "panics": 1}
    bucket = state.by_minute["2026-05-03T02:00"]
    assert bucket.level_counts == {"info": 8, "warn": 2}
    assert bucket.event_counts == {"api: served": 7, "api: rejected": 1}
    assert bucket.component_counts == {"api": 8, "worker": 2}


def test_merge_two_payloads_sums_counts() -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    merge_iteration(state, _sample_payload())
    assert state.total_lines == 200
    assert state.warn_error_counts == {"timeouts": 8, "panics": 2}
    bucket = state.by_minute["2026-05-03T02:00"]
    assert bucket.level_counts == {"info": 16, "warn": 4}
    assert bucket.event_counts == {"api: served": 14, "api: rejected": 2}


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    state.processed_files = {"a.json": "1:2"}
    save_state(tmp_path, state)
    restored = load_state(tmp_path)
    assert restored.total_lines == state.total_lines
    assert restored.failed_to_parse == state.failed_to_parse
    assert restored.warn_error_counts == state.warn_error_counts
    assert restored.processed_files == {"a.json": "1:2"}
    assert (
        restored.by_minute["2026-05-03T02:00"].level_counts
        == state.by_minute["2026-05-03T02:00"].level_counts
    )


def test_load_state_returns_empty_when_file_missing(tmp_path: Path) -> None:
    state = load_state(tmp_path)
    assert state.total_lines == 0
    assert state.by_minute == {}


def test_load_state_tolerates_corrupt_state(tmp_path: Path) -> None:
    (tmp_path / ".aggregate_data.json").write_text("not json", encoding="utf-8")
    state = load_state(tmp_path)
    assert state.total_lines == 0


def test_write_time_series_emits_levels_in_canonical_order(tmp_path: Path) -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    csv_path = tmp_path / "time_series.csv"
    write_time_series(csv_path, state)
    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    assert rows[0] == ["timestamp", "debug", "info", "warn", "error"]
    assert rows[1] == ["2026-05-03T02:00", "0", "8", "2", "0"]
    assert rows[2] == ["2026-05-03T02:01", "0", "6", "0", "1"]


def test_write_events_csv_keeps_top_n_events(tmp_path: Path) -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    csv_path = tmp_path / "events_per_minute.csv"
    write_events_csv(csv_path, state, top_n=2)
    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    # Header columns are the top-2 events globally, sorted by total count.
    assert rows[0][0] == "timestamp"
    assert "api: served" in rows[0]


def test_write_components_csv_lists_every_component(tmp_path: Path) -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    csv_path = tmp_path / "components_per_minute.csv"
    write_components_csv(csv_path, state)
    rows = list(csv.reader(csv_path.open(encoding="utf-8")))
    assert rows[0] == ["timestamp", "api", "worker"]


def test_write_summary_renders_markdown(tmp_path: Path) -> None:
    state = AggregateState()
    merge_iteration(state, _sample_payload())
    summary = tmp_path / "summary.md"
    write_summary(summary, state, new_files_count=1)
    text = summary.read_text(encoding="utf-8")
    assert "# Log aggregation summary" in text
    assert "**New files processed:** 1" in text
    assert "## Top events" in text
    assert "api: served" in text


def test_write_summary_escapes_pipes_in_event_labels(tmp_path: Path) -> None:
    state = AggregateState()
    state.by_minute["2026-05-03T02:00"] = MinuteBucket(event_counts={"weird | event": 3})
    summary = tmp_path / "summary.md"
    write_summary(summary, state, new_files_count=0)
    text = summary.read_text(encoding="utf-8")
    assert "weird \\| event" in text


def test_aggregate_processes_iteration_files(tmp_path: Path) -> None:
    (tmp_path / "iter_001.json").write_text(json.dumps(_sample_payload()), encoding="utf-8")
    assert aggregate(tmp_path) is True
    assert (tmp_path / "time_series.csv").exists()
    assert (tmp_path / "events_per_minute.csv").exists()
    assert (tmp_path / "components_per_minute.csv").exists()
    assert (tmp_path / "summary.md").exists()
    assert (tmp_path / ".aggregate_data.json").exists()


def test_aggregate_skips_already_processed_files(tmp_path: Path) -> None:
    iter_file = tmp_path / "iter_001.json"
    iter_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    aggregate(tmp_path)
    state = load_state(tmp_path)
    first_total = state.total_lines
    # Re-running with no changes should not double-count.
    aggregate(tmp_path)
    state = load_state(tmp_path)
    assert state.total_lines == first_total


def test_aggregate_rebuilds_from_scratch_when_file_rewritten(tmp_path: Path) -> None:
    iter_file = tmp_path / "iter_001.json"
    iter_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    aggregate(tmp_path)
    # Rewrite with doubled meta totals — different fingerprint, same name.
    bigger = _sample_payload()
    bigger["meta"] = {"total_lines": 200, "failed_to_parse": 10}
    iter_file.write_text(json.dumps(bigger), encoding="utf-8")
    # Touch mtime so fingerprint changes deterministically on fast filesystems.
    import os

    os.utime(iter_file, ns=(2_000_000_000, 2_000_000_000))
    aggregate(tmp_path)
    state = load_state(tmp_path)
    # Rebuilt from scratch — total_lines reflects the new file alone, not stacked.
    assert state.total_lines == 200


def test_aggregate_returns_false_when_run_dir_missing(tmp_path: Path) -> None:
    assert aggregate(tmp_path / "nope") is False


def test_cli_full_flag_rebuilds(tmp_path: Path) -> None:
    iter_file = tmp_path / "iter_001.json"
    iter_file.write_text(json.dumps(_sample_payload()), encoding="utf-8")
    assert cli([str(tmp_path)]) == 0
    assert cli([str(tmp_path), "--full"]) == 0


def test_cli_returns_non_zero_when_dir_missing(tmp_path: Path) -> None:
    assert cli([str(tmp_path / "missing")]) == 1
