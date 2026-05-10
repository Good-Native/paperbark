"""Cloudflare Wrangler log source.

Wraps ``wrangler tail <worker> --format=json`` for one Cloudflare
Worker per ``[[sources]]`` entry. Each :meth:`capture` call spawns a
fresh subprocess, reads events for ``samples_window_seconds``, then
terminates the process and yields the bounded set of decorated lines.

Two non-obvious behaviours that distinguish this from
:class:`paperbark.sources.flyctl.FlyctlSource`:

1. ``wrangler tail --format=json`` (wrangler 4.x) emits *pretty-printed*
   JSON, not NDJSON. Each event spans many lines of indented output.
   We therefore stream stdout into ``json.JSONDecoder.raw_decode`` and
   yield one parsed dict per top-level object, rather than iterating
   ``Popen.stdout`` line by line.
2. ``wrangler tail`` is a live stream with no ``--no-tail`` equivalent.
   We bound each iteration's capture by wall-clock time
   (``samples_window_seconds``, default 5 s) instead of by snapshot
   window, and cap the number of decorated lines by ``samples``.

Each decoded event is decorated before it leaves the source:

- The line is prefixed with an ISO-8601 timestamp derived from
  ``eventTimestamp`` (ms epoch). This keeps the default cursor filter
  on its leading-ISO path; without the prefix the cursor would drop
  every line because wrangler payloads have no leading timestamp.
- A synthetic ``level`` key is injected from ``outcome``
  (``ok`` → ``info``, ``exception`` / ``exceededCpu`` → ``error``,
  ``canceled`` / ``unknown`` → ``warn``) so the default
  :class:`JsonKeysFormat` populates the canonical record's level
  field. Operators who want a different mapping can override
  ``format_keys.level`` to point at their own field.
- ``scriptName`` is mapped to ``component`` by default via
  ``format_keys`` when the operator hasn't supplied one. Multi-Worker
  runs that want a per-source override can replace this.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import deque
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from paperbark.formats import Format

_WRANGLER_TIMEOUT = 5.0
"""Seconds to wait after ``terminate()`` before sending SIGKILL."""

DEFAULT_SAMPLES = 400
"""Per-iteration line cap. Mirrors ``FlyctlSource``'s default."""

DEFAULT_WINDOW_SECONDS = 5
"""Default per-iteration capture window. ``wrangler tail`` is a live
stream, so we time-bound it rather than rely on a snapshot flag."""

_OUTCOME_TO_LEVEL: dict[str, str] = {
    "ok": "info",
    "exception": "error",
    "exceededCpu": "error",
    "canceled": "warn",
    "unknown": "warn",
}
"""Default Cloudflare ``outcome`` → log level mapping. Anything not
listed maps to ``warn`` so unknown future outcomes still surface."""


def _stream_json_objects(stream: IO[str]) -> Iterator[dict[str, Any]]:
    """Yield top-level JSON objects from ``stream``.

    Wrangler's ``--format=json`` emits pretty-printed objects without a
    delimiter between them. ``json.JSONDecoder.raw_decode`` consumes one
    JSON value from a buffer and reports where it stopped, so we keep a
    rolling buffer and decode as much as we can each time new bytes
    arrive. Robust against indented payloads, strings containing
    braces, and partial reads.
    """
    decoder = json.JSONDecoder()
    buffer = ""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        buffer += chunk
        buffer = buffer.lstrip()
        while buffer:
            try:
                obj, idx = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                # Not enough data yet; wait for the next read.
                break
            if isinstance(obj, dict):
                yield obj
            buffer = buffer[idx:].lstrip()


def _default_runner(
    command: list[str], window_seconds: float, env: dict[str, str]
) -> Iterator[dict[str, Any]]:
    """Run ``command`` for ``window_seconds`` and yield parsed events.

    Spawns a child process with the given ``env`` overrides on top of
    the parent environment, reads its stdout into a JSON object stream,
    and stops yielding once the time window elapses. Cleans up on early
    exit (terminate → wait 5 s → kill).
    """
    full_env = {**os.environ, **env}
    # Command is operator-configured and executed without a shell.
    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=full_env,
    )
    if process.stdout is None:
        raise RuntimeError("wrangler process started without a stdout pipe")
    deadline = time.monotonic() + window_seconds
    try:
        for event in _stream_json_objects(process.stdout):
            yield event
            if time.monotonic() >= deadline:
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_WRANGLER_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _event_to_line(event: dict[str, Any]) -> str | None:
    """Decorate a wrangler event as one paperbark-ready line.

    Returns ``None`` for events without a usable ``eventTimestamp``;
    those would fail the cursor filter anyway, and dropping them at
    the source keeps the rest of the pipeline simple.
    """
    raw_ts = event.get("eventTimestamp")
    if not isinstance(raw_ts, (int, float)):
        return None
    iso = datetime.fromtimestamp(raw_ts / 1000.0, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcome = event.get("outcome")
    if isinstance(outcome, str) and "level" not in event:
        decorated = {**event, "level": _OUTCOME_TO_LEVEL.get(outcome, "warn")}
    else:
        decorated = event
    return f"{iso} {json.dumps(decorated, separators=(',', ':'))}\n"


class WranglerSource:
    """``wrangler tail`` source for one Cloudflare Worker."""

    name = "wrangler"

    def __init__(
        self,
        worker: str,
        *,
        account_id: str | None = None,
        samples_window_seconds: float = DEFAULT_WINDOW_SECONDS,
        samples: int = DEFAULT_SAMPLES,
        format_keys: dict[str, tuple[str, ...]] | None = None,
        line_format: Format | None = None,
        runner: Callable[[list[str], float, dict[str, str]], Iterator[dict[str, Any]]]
        | None = None,
    ) -> None:
        if not worker:
            raise ValueError("WranglerSource requires a non-empty worker name")
        if samples <= 0:
            raise ValueError(f"WranglerSource samples must be > 0, got {samples}")
        if samples_window_seconds <= 0:
            raise ValueError(
                f"WranglerSource samples_window_seconds must be > 0, got {samples_window_seconds}"
            )
        self.worker = worker
        self.account_id = account_id
        self.samples_window_seconds = samples_window_seconds
        self.samples = samples
        # Default ``component`` to ``scriptName`` so most users don't have to
        # set ``format_keys = { component = "scriptName" }`` themselves.
        # Operator-supplied overrides win.
        merged_keys: dict[str, tuple[str, ...]] = {"component": ("scriptName",)}
        if format_keys:
            merged_keys.update(format_keys)
        self.format_keys = merged_keys
        self.line_format = line_format
        self._runner = runner or _default_runner

    @property
    def command(self) -> list[str]:
        return ["wrangler", "tail", self.worker, "--format=json"]

    def _env(self) -> dict[str, str]:
        if self.account_id:
            return {"CLOUDFLARE_ACCOUNT_ID": self.account_id}
        return {}

    def capture(self, *, since: str = "") -> Iterator[str]:
        # ``wrangler tail`` has no --since equivalent; cursor filter
        # handles bounding regardless.
        del since
        events = self._runner(self.command, self.samples_window_seconds, self._env())
        # Cap at ``samples`` so a busy Worker can't blow memory mid-iteration.
        bounded: deque[dict[str, Any]] = deque(events, maxlen=self.samples)
        for event in bounded:
            line = _event_to_line(event)
            if line is not None:
                yield line
