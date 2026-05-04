"""Tests for paperbark.duration."""

from __future__ import annotations

import pytest

from paperbark.duration import format_elapsed, parse_duration


class TestParseDuration:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("0", 0),
            ("30", 30),
            ("30s", 30),
            ("5m", 300),
            ("1h", 3600),
            ("2h", 7200),
            ("90s", 90),
            ("  45s  ", 45),
        ],
    )
    def test_accepts_supported_forms(self, value: str, expected: int) -> None:
        assert parse_duration(value) == expected

    def test_accepts_int_passthrough(self) -> None:
        assert parse_duration(42) == 42

    def test_zero_is_allowed(self) -> None:
        # 0 is the documented "disabled" sentinel for --analyse-every.
        assert parse_duration("0") == 0
        assert parse_duration(0) == 0

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "  ",
            "5x",
            "5min",
            "1h30m",  # combined form unsupported, matches bash behaviour
            "abc",
            "-5s",
            "-1",
            "5.5s",
            "5 s",  # internal whitespace
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValueError, match="invalid duration"):
            parse_duration(value)

    def test_rejects_negative_int(self) -> None:
        with pytest.raises(ValueError, match="invalid duration"):
            parse_duration(-1)

    def test_rejects_non_string_non_int(self) -> None:
        with pytest.raises(TypeError):
            parse_duration(1.5)  # type: ignore[arg-type]


class TestFormatElapsed:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "0s"),
            (1, "1s"),
            (59, "59s"),
            (60, "1m 0s"),
            (75, "1m 15s"),
            (3599, "59m 59s"),
            (3600, "1h 0m"),
            (3660, "1h 1m"),
            (7325, "2h 2m"),
        ],
    )
    def test_format(self, seconds: int, expected: str) -> None:
        assert format_elapsed(seconds) == expected

    def test_negative_clamps_to_zero(self) -> None:
        # Clock skew between iter_start and now during a state read could
        # produce a tiny negative; degrade gracefully rather than emit "-1s".
        assert format_elapsed(-3) == "0s"
