"""Fly.io log source.

Wraps ``flyctl logs`` in ``--no-tail`` mode (the only mode v1 supports).
Each call to :meth:`capture` runs a fresh subprocess and yields its
stdout line by line; the cursor filter (``paperbark.cursor``) is
responsible for dedup across overlapping iterations because Fly's
``--no-tail`` returns the same recent window every time.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator

_FLYCTL_TIMEOUT = 5.0  # seconds; how long to wait after terminate() before SIGKILL.


def _default_runner(command: list[str]) -> Iterator[str]:
    """Run ``command`` and yield its stdout lines.

    Cleans up the child process on early exit (consumer ``break``s,
    raises, or generator is closed).
    """
    # The command list is built from operator-configured values; it is
    # invoked without a shell and we never concatenate untrusted strings into
    # a single arg. Bandit S603 is a generic "subprocess invocation" warning
    # rather than a real risk here.
    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError("flyctl process started without a stdout pipe")
    try:
        yield from process.stdout
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_FLYCTL_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class FlyctlSource:
    """``flyctl logs`` source for one Fly.io app."""

    name = "flyctl"

    def __init__(
        self,
        app: str,
        *,
        no_tail: bool = True,
        runner: Callable[[list[str]], Iterator[str]] | None = None,
    ) -> None:
        if not app:
            raise ValueError("FlyctlSource requires a non-empty app name")
        self.app = app
        self.no_tail = no_tail
        self._runner = runner or _default_runner

    @property
    def command(self) -> list[str]:
        cmd = ["flyctl", "logs", "-a", self.app]
        if self.no_tail:
            cmd.append("--no-tail")
        return cmd

    def capture(self, *, since: str = "") -> Iterator[str]:
        # `flyctl logs` has no native --since flag; the cursor filter handles
        # bounding. We drop the parameter rather than fail noisily — the
        # contract still holds because cursor filtering is mandatory anyway.
        del since
        return self._runner(self.command)
