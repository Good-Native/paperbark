"""Tests for the source layer."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from paperbark.sources import (
    CloudWatchSource,
    FileSource,
    FlyctlSource,
    KubectlSource,
    Source,
    StdinSource,
    WranglerSource,
    registered_sources,
)


def test_registry_lists_every_source_by_name() -> None:
    names = set(registered_sources())
    assert names == {"flyctl", "wrangler", "kubectl", "cloudwatch", "file", "stdin"}


def test_flyctl_source_satisfies_protocol() -> None:
    source = FlyctlSource(app="example", runner=lambda _cmd: iter(()))
    assert isinstance(source, Source)


def test_flyctl_command_includes_app_and_no_tail_by_default() -> None:
    source = FlyctlSource(app="example", runner=lambda _cmd: iter(()))
    # ``flyctl logs`` itself has no native ``-n`` flag for line count
    # (``-n`` aliases ``--no-tail``), so the command stays minimal. The
    # ``samples`` knob is enforced inside ``capture()`` via a bounded
    # deque, mirroring the bash dispatcher's ``| tail -n <samples>``.
    assert source.command == ["flyctl", "logs", "-a", "example", "--no-tail"]


def test_flyctl_command_drops_no_tail_when_disabled() -> None:
    source = FlyctlSource(app="example", no_tail=False, runner=lambda _cmd: iter(()))
    assert source.command == ["flyctl", "logs", "-a", "example"]


def test_flyctl_capture_keeps_last_samples_lines() -> None:
    """``samples=2`` must drop everything but the last two yielded lines."""

    def fake_runner(_command: list[str]) -> Iterator[str]:
        yield "2026-05-03T02:00:01Z first\n"
        yield "2026-05-03T02:00:02Z second\n"
        yield "2026-05-03T02:00:03Z third\n"

    source = FlyctlSource(app="example", samples=2, runner=fake_runner)
    assert list(source.capture()) == [
        "2026-05-03T02:00:02Z second\n",
        "2026-05-03T02:00:03Z third\n",
    ]


@pytest.mark.parametrize("bad_samples", [0, -1, -400])
def test_flyctl_rejects_zero_or_negative_samples(bad_samples: int) -> None:
    with pytest.raises(ValueError, match="samples must be > 0"):
        FlyctlSource(app="example", samples=bad_samples)


def test_flyctl_capture_yields_lines_from_runner() -> None:
    captured: list[list[str]] = []

    def fake_runner(command: list[str]) -> Iterator[str]:
        captured.append(command)
        yield "2026-05-03T02:00:01Z first\n"
        yield "2026-05-03T02:00:02Z second\n"

    source = FlyctlSource(app="example", runner=fake_runner)
    assert list(source.capture()) == [
        "2026-05-03T02:00:01Z first\n",
        "2026-05-03T02:00:02Z second\n",
    ]
    assert captured == [["flyctl", "logs", "-a", "example", "--no-tail"]]


def test_flyctl_requires_app_name() -> None:
    with pytest.raises(ValueError, match="non-empty app name"):
        FlyctlSource(app="")


@pytest.mark.parametrize(
    "stub_class",
    [KubectlSource, CloudWatchSource],
)
def test_stub_sources_raise_not_implemented_on_capture(
    stub_class: type[Source],
) -> None:
    source = stub_class()
    with pytest.raises(NotImplementedError):
        list(source.capture())


def test_stubs_are_protocol_compatible() -> None:
    for stub_class in (
        KubectlSource,
        CloudWatchSource,
    ):
        instance = stub_class()
        assert isinstance(instance, Source)


# --- v0.2: real file source -----------------------------------------------


def test_file_source_yields_lines_from_disk(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    source = FileSource(path=log)
    assert list(source.capture()) == ["alpha\n", "bravo\n", "charlie\n"]


def test_file_source_satisfies_protocol(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("", encoding="utf-8")
    source = FileSource(path=log)
    assert isinstance(source, Source)


def test_file_source_requires_non_empty_path() -> None:
    with pytest.raises(ValueError, match="non-empty path"):
        FileSource(path="")


def test_file_source_capture_raises_when_file_missing(tmp_path: Path) -> None:
    source = FileSource(path=tmp_path / "does-not-exist.log")
    with pytest.raises(FileNotFoundError):
        list(source.capture())


def test_file_source_decodes_with_encoding(tmp_path: Path) -> None:
    log = tmp_path / "latin.log"
    log.write_bytes("café\n".encode("latin-1"))
    source = FileSource(path=log, encoding="latin-1")
    assert list(source.capture()) == ["café\n"]


def test_file_source_replaces_undecodable_bytes(tmp_path: Path) -> None:
    """A stray byte that can't decode as UTF-8 must not abort capture —
    we'd rather emit a replacement char than drop a record entirely."""
    log = tmp_path / "mixed.log"
    log.write_bytes(b"good\n\xff bad\n")
    source = FileSource(path=log)
    lines = list(source.capture())
    assert lines[0] == "good\n"
    assert "bad" in lines[1]


# --- v0.2: real stdin source ----------------------------------------------


def test_stdin_source_yields_lines_from_injected_stream() -> None:
    source = StdinSource(stream=io.StringIO("alpha\nbravo\ncharlie\n"))
    assert list(source.capture()) == ["alpha\n", "bravo\n", "charlie\n"]


def test_stdin_source_satisfies_protocol() -> None:
    source = StdinSource(stream=io.StringIO(""))
    assert isinstance(source, Source)


def test_stdin_source_yields_nothing_on_empty_stream() -> None:
    source = StdinSource(stream=io.StringIO(""))
    assert list(source.capture()) == []


def test_stdin_source_uses_sys_stdin_when_no_stream_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello\nworld\n"))
    source = StdinSource()
    assert list(source.capture()) == ["hello\n", "world\n"]


def test_stdin_source_second_capture_after_eof_yields_nothing() -> None:
    """A piped stdin is a single-use stream. The first ``capture()`` drains
    it; subsequent calls must not raise but yield nothing — long-running
    monitor loops over a stdin pipe see one productive iteration followed
    by empties."""
    source = StdinSource(stream=io.StringIO("only-line\n"))
    assert list(source.capture()) == ["only-line\n"]
    assert list(source.capture()) == []


# --- v0.2: real wrangler source -------------------------------------------


_WRANGLER_EVENT_OK = {
    "outcome": "ok",
    "scriptName": "demo-worker",
    "eventTimestamp": 1715245200000,
    "event": {"request": {"url": "https://example.com/", "method": "GET"}},
    "logs": [],
    "exceptions": [],
}
_WRANGLER_EVENT_EXCEPTION = {
    "outcome": "exception",
    "scriptName": "demo-worker",
    "eventTimestamp": 1715245260000,
    "event": {"request": {"url": "https://example.com/oops", "method": "POST"}},
    "logs": [],
    "exceptions": [{"name": "Error", "message": "boom"}],
}


def _fake_wrangler_runner(
    events: list[dict[str, object]],
) -> Callable[[list[str], float, dict[str, str]], Iterator[dict[str, object]]]:
    def runner(
        _cmd: list[str], _window: float, _env: dict[str, str]
    ) -> Iterator[dict[str, object]]:
        yield from events

    return runner


def test_wrangler_source_satisfies_protocol() -> None:
    source = WranglerSource(worker="demo-worker", runner=_fake_wrangler_runner([]))
    assert isinstance(source, Source)


def test_wrangler_source_requires_non_empty_worker() -> None:
    with pytest.raises(ValueError, match="non-empty worker name"):
        WranglerSource(worker="")


def test_wrangler_source_rejects_non_positive_samples() -> None:
    with pytest.raises(ValueError, match="samples must be > 0"):
        WranglerSource(worker="demo-worker", samples=0)


def test_wrangler_source_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="samples_window_seconds must be > 0"):
        WranglerSource(worker="demo-worker", samples_window_seconds=0)


def test_wrangler_source_command_shape() -> None:
    source = WranglerSource(worker="demo-worker", runner=_fake_wrangler_runner([]))
    assert source.command == ["wrangler", "tail", "demo-worker", "--format=json"]


def test_wrangler_source_iso_prefixes_each_line() -> None:
    """Lines must lead with ``YYYY-MM-DDTHH:MM:SSZ`` derived from
    ``eventTimestamp`` so the cursor filter's leading-ISO path accepts
    them."""
    source = WranglerSource(
        worker="demo-worker",
        runner=_fake_wrangler_runner([_WRANGLER_EVENT_OK, _WRANGLER_EVENT_EXCEPTION]),
    )
    lines = list(source.capture())
    assert len(lines) == 2
    assert lines[0].startswith("2024-05-09T09:00:00Z ")
    assert lines[1].startswith("2024-05-09T09:01:00Z ")


def test_wrangler_source_injects_level_from_outcome() -> None:
    import json as _json

    source = WranglerSource(
        worker="demo-worker",
        runner=_fake_wrangler_runner([_WRANGLER_EVENT_OK, _WRANGLER_EVENT_EXCEPTION]),
    )
    lines = list(source.capture())
    ok_payload = _json.loads(lines[0].split(" ", 1)[1])
    err_payload = _json.loads(lines[1].split(" ", 1)[1])
    assert ok_payload["level"] == "info"
    assert err_payload["level"] == "error"


def test_wrangler_source_drops_events_without_timestamp() -> None:
    """An event with no ``eventTimestamp`` can't pass the cursor filter
    anyway. Drop it at the source rather than emitting a half-broken
    line."""
    source = WranglerSource(
        worker="demo-worker",
        runner=_fake_wrangler_runner([{"outcome": "ok", "scriptName": "demo-worker"}]),
    )
    assert list(source.capture()) == []


def test_wrangler_source_format_keys_default_maps_component_to_script_name() -> None:
    """Most users won't set ``format_keys`` — the default should already
    pick up ``scriptName`` as ``component``."""
    source = WranglerSource(worker="demo-worker", runner=_fake_wrangler_runner([]))
    assert source.format_keys is not None
    assert source.format_keys["component"] == ("scriptName",)


def test_wrangler_source_user_format_keys_override_default() -> None:
    source = WranglerSource(
        worker="demo-worker",
        format_keys={"component": ("custom-key",), "level": ("severity",)},
        runner=_fake_wrangler_runner([]),
    )
    assert source.format_keys is not None
    assert source.format_keys["component"] == ("custom-key",)
    assert source.format_keys["level"] == ("severity",)


def test_wrangler_source_runner_receives_account_id_env() -> None:
    captured: dict[str, object] = {}

    def capturing_runner(
        cmd: list[str], window: float, env: dict[str, str]
    ) -> Iterator[dict[str, object]]:
        captured["cmd"] = cmd
        captured["window"] = window
        captured["env"] = env
        return iter(())

    source = WranglerSource(
        worker="demo-worker",
        account_id="acct-123",
        samples_window_seconds=7,
        runner=capturing_runner,
    )
    list(source.capture())
    assert captured["cmd"] == ["wrangler", "tail", "demo-worker", "--format=json"]
    assert captured["window"] == 7
    assert captured["env"] == {"CLOUDFLARE_ACCOUNT_ID": "acct-123"}


def test_wrangler_source_runner_env_empty_without_account_id() -> None:
    captured: dict[str, object] = {}

    def capturing_runner(
        cmd: list[str], window: float, env: dict[str, str]
    ) -> Iterator[dict[str, object]]:
        captured["env"] = env
        return iter(())

    source = WranglerSource(worker="demo-worker", runner=capturing_runner)
    list(source.capture())
    assert captured["env"] == {}


def test_wrangler_source_caps_samples() -> None:
    """If wrangler outpaces our buffer the source should keep the last
    ``samples`` events, mirroring the bash dispatcher's
    ``| tail -n <samples>`` semantics."""
    events = [{**_WRANGLER_EVENT_OK, "eventTimestamp": 1715245200000 + i} for i in range(10)]
    source = WranglerSource(
        worker="demo-worker",
        samples=3,
        runner=_fake_wrangler_runner(events),
    )
    lines = list(source.capture())
    assert len(lines) == 3


def test_wrangler_stream_json_objects_handles_pretty_printed_input() -> None:
    """Wrangler 4.x emits pretty-printed JSON, not NDJSON. The reader
    must handle multi-line objects with strings containing braces."""
    from paperbark.sources.wrangler import _stream_json_objects

    pretty = (
        "{\n"
        '  "outcome": "ok",\n'
        '  "scriptName": "demo",\n'
        '  "msg": "string with } brace",\n'
        '  "eventTimestamp": 1715245200000\n'
        "}\n"
        "{\n"
        '  "outcome": "exception",\n'
        '  "eventTimestamp": 1715245260000\n'
        "}\n"
    )
    objs = list(_stream_json_objects(io.StringIO(pretty)))
    assert len(objs) == 2
    assert objs[0]["outcome"] == "ok"
    assert objs[0]["msg"] == "string with } brace"
    assert objs[1]["outcome"] == "exception"


def test_wrangler_default_runner_raises_on_non_zero_exit() -> None:
    """An auth failure or unknown-account error from wrangler must
    surface as ``WranglerProcessError`` with the captured stderr —
    not a silent zero-line iteration."""
    from paperbark.sources.wrangler import WranglerProcessError, _default_runner

    # ``sh -c '... exit 2'`` exits 2 with stderr text and no stdout —
    # mimics a wrangler auth/CLI failure cleanly without needing
    # wrangler installed.
    fake_command = ["sh", "-c", "echo 'auth failed' >&2; exit 2"]
    with pytest.raises(WranglerProcessError, match=r"exited with code 2.*auth failed"):
        list(_default_runner(fake_command, window_seconds=2.0, env={}))


def test_wrangler_default_runner_terminates_idle_stream_within_window() -> None:
    """The wall-clock window must fire even when the child writes
    nothing. Regression guard: the reader runs in a background thread
    so a blocking ``read`` can't hold the iteration past the deadline."""
    import time as _time

    from paperbark.sources.wrangler import _default_runner

    # ``cat`` with no input blocks on stdin forever — perfect stand-in for
    # a quiet wrangler tail. With a 1 s window the iteration must return
    # within a few seconds (slack for thread / subprocess teardown).
    fake_command = ["cat"]
    started = _time.monotonic()
    events = list(_default_runner(fake_command, window_seconds=1.0, env={}))
    elapsed = _time.monotonic() - started
    assert events == []
    assert elapsed < 6.0
