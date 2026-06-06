"""Shared subprocess helpers for log sources.

Sources spawn their upstream CLI (``flyctl``, ``wrangler``, …) with
``subprocess.Popen(command, shell=False)``. On Windows that routes through
``CreateProcess``, which — unlike the shell — does **not** consult
``PATHEXT``. A bare ``"wrangler"`` therefore fails to find the npm-installed
``wrangler.cmd`` shim and raises ``FileNotFoundError`` (``WinError 2``), even
though the tool is correctly on ``PATH``. ``flyctl`` happens to ship a real
``flyctl.exe`` so it dodges this, but the fragility is shared.

:func:`resolve_executable` resolves ``command[0]`` via :func:`shutil.which`,
which honours ``PATHEXT`` on Windows (returning the full ``…\\wrangler.cmd``
path) and is a no-op-style lookup on POSIX. When the executable can't be
found we return the command unchanged so the original ``FileNotFoundError``
still surfaces — the behaviour is identical to before for a genuinely
missing tool.
"""

from __future__ import annotations

import shutil


def resolve_executable(command: list[str]) -> list[str]:
    """Return ``command`` with ``command[0]`` resolved to a full path.

    Uses :func:`shutil.which` so the lookup honours ``PATHEXT`` on Windows
    (a bare ``wrangler`` resolves to the ``wrangler.cmd`` shim) before the
    list reaches ``subprocess.Popen``. Returns the command unchanged when it
    is empty or the executable can't be found on ``PATH``, preserving the
    prior ``FileNotFoundError`` for a missing tool.
    """
    if not command:
        return command
    resolved = shutil.which(command[0])
    if resolved is None:
        return command
    return [resolved, *command[1:]]
