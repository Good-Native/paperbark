"""Tests for the source layer."""

from __future__ import annotations

from collections.abc import Iterator
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
    [WranglerSource, KubectlSource, CloudWatchSource, StdinSource],
)
def test_stub_sources_raise_not_implemented_on_capture(
    stub_class: type[Source],
) -> None:
    source = stub_class()
    with pytest.raises(NotImplementedError):
        list(source.capture())


def test_stubs_are_protocol_compatible() -> None:
    for stub_class in (
        WranglerSource,
        KubectlSource,
        CloudWatchSource,
        StdinSource,
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
