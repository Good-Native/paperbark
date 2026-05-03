"""Duration parsing and formatting helpers.

Used by ``--interval``, ``--analyse-every``, and the ticker's elapsed-time
display. Mirrors the supported forms in ``reference/logs.sh`` (``parse_duration``
and ``fmt_duration``) so the Python port behaves identically: plain integer
seconds, ``Ns``, ``Nm``, or ``Nh``. Combined forms like ``1h30m`` are deliberately
unsupported — the bash version doesn't accept them either, and admitting them
here would silently widen the contract.
"""

from __future__ import annotations

import re

_DURATION_RE = re.compile(r"^(\d+)([smh]?)$")
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration(value: str | int) -> int:
    """Parse ``value`` into a non-negative integer count of seconds.

    Accepted: a non-negative ``int``; or a string of digits optionally suffixed
    with ``s``/``m``/``h``. Whitespace at the ends is tolerated; internal
    whitespace, decimals, signs, and unknown suffixes raise ``ValueError``.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly.
        raise TypeError(f"invalid duration: {value!r} (bool is not a duration)")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"invalid duration: {value!r} (must be >= 0)")
        return value
    if not isinstance(value, str):
        raise TypeError(f"invalid duration type: {type(value).__name__}")
    stripped = value.strip()
    match = _DURATION_RE.match(stripped)
    if not match:
        raise ValueError(f"invalid duration: {value!r} (use 30s, 5m, 1h, or plain seconds)")
    number, unit = match.groups()
    return int(number) * _UNIT_SECONDS[unit]


def format_elapsed(seconds: int) -> str:
    """Format ``seconds`` for the ticker's elapsed-time field.

    Matches ``fmt_duration`` in the bash dispatcher: ``Ns`` under a minute,
    ``Nm Ns`` under an hour, ``Nh Nm`` otherwise. Negative input clamps to
    zero so a small clock-skew read between threads renders cleanly.
    """
    s = max(seconds, 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"
