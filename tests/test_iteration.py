"""Tests for paperbark.iteration."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from paperbark.aggregate import AggregateState, merge_iteration
from paperbark.iteration import (
    cli,
    summarise_lines,
    summarise_log_file,
    write_flat_csv,
)


def _line(level: str, component: str, message: str, ts: str = "2026-05-03T02:00:01Z") -> str:
    payload = {"time": ts, "level": level, "component": component, "msg": message}
    return f"prefix {json.dumps(payload)}\n"


def test_summarise_buckets_counts_by_minute() -> None:
    summary = summarise_lines(
        [
            _line("info", "api", "served"),
            _line("info", "api", "served", ts="2026-05-03T02:00:30Z"),
            _line("warn", "worker", "stuck", ts="2026-05-03T02:01:00Z"),
        ]
    )
    assert summary["meta"]["total_lines"] == 3
    assert summary["meta"]["parsed"] == 3
    assert summary["meta"]["failed_to_parse"] == 0
    assert summary["level_counts"] == {
        "2026-05-03T02:00": {"info": 2},
        "2026-05-03T02:01": {"warn": 1},
    }
    assert summary["component_counts"] == {
        "2026-05-03T02:00": {"api": 2},
        "2026-05-03T02:01": {"worker": 1},
    }
    assert summary["warn_error_counts"] == {"worker: stuck": 1}


def test_unparseable_lines_count_as_failed() -> None:
    summary = summarise_lines(
        [
            "no json here\n",
            '{"definitely":"not closed\n',
            _line("info", "api", "ok"),
        ]
    )
    assert summary["meta"]["total_lines"] == 3
    assert summary["meta"]["parsed"] == 1
    assert summary["meta"]["failed_to_parse"] == 2


def test_event_counts_sorted_by_descending_count() -> None:
    summary = summarise_lines(
        [
            _line("info", "api", "common"),
            _line("info", "api", "common"),
            _line("info", "api", "common"),
            _line("info", "api", "rare"),
        ]
    )
    events = summary["event_counts"]["2026-05-03T02:00"]
    assert events[0] == {"event": "api: common", "count": 3}
    assert events[1] == {"event": "api: rare", "count": 1}


def test_component_prefix_stripped_from_message() -> None:
    summary = summarise_lines(
        [_line("info", "api", "[api] served request")],
    )
    events = summary["event_counts"]["2026-05-03T02:00"]
    assert events[0]["event"] == "api: served request"


def test_unknown_component_keeps_message_intact() -> None:
    summary = summarise_lines(
        ['prefix {"time":"2026-05-03T02:00:01Z","level":"info","msg":"[api] keep me"}\n'],
    )
    events = summary["event_counts"]["2026-05-03T02:00"]
    assert events[0]["event"] == "unknown: [api] keep me"


def test_missing_timestamp_buckets_under_unknown() -> None:
    summary = summarise_lines(
        ['prefix {"level":"info","component":"api","msg":"x"}\n'],
    )
    assert "unknown" in summary["level_counts"]


def test_summary_round_trips_through_aggregate(tmp_path: Path) -> None:
    summary = summarise_lines(
        [
            _line("info", "api", "served"),
            _line("warn", "worker", "stuck", ts="2026-05-03T02:01:00Z"),
        ]
    )
    state = AggregateState()
    merge_iteration(state, summary)
    assert state.warn_error_counts == {"worker: stuck": 1}
    assert state.by_minute["2026-05-03T02:00"].component_counts == {"api": 1}


def test_summarise_log_file_writes_flat_csv(tmp_path: Path) -> None:
    raw = tmp_path / "raw.log"
    raw.write_text(
        _line("info", "api", "served")
        + 'prefix {"time":"2026-05-03T02:01:00Z","level":"warn","component":"worker",'
        '"msg":"stuck","extra_field":42}\n',
        encoding="utf-8",
    )
    flat = tmp_path / "flat.csv"
    summary = summarise_log_file(raw, flat_csv_path=flat)
    assert summary["meta"]["parsed"] == 2
    rows = list(csv.DictReader(flat.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["component"] == "api"
    assert rows[0]["message"] == "served"
    assert rows[0]["extras"] == ""
    assert rows[1]["extras"] == '{"extra_field":42}'


def test_write_flat_csv_writes_header_only_when_no_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "empty.csv"
    write_flat_csv(csv_path, [])
    text = csv_path.read_text(encoding="utf-8").splitlines()
    assert text == ["timestamp,level,component,message,extras"]


def test_cli_writes_summary_and_flat_csv(tmp_path: Path) -> None:
    raw = tmp_path / "raw.log"
    raw.write_text(_line("info", "api", "served"), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = cli([str(raw), str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["meta"]["parsed"] == 1
    assert (tmp_path / "out.csv").exists()


def test_cli_reports_missing_input(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    assert cli([str(tmp_path / "nope.log"), str(out)]) == 1


def test_cli_rejects_wrong_arg_count() -> None:
    assert cli([]) == 1
    assert cli(["only-one"]) == 1
    assert cli(["a", "b", "c"]) == 1
