"""AWS CloudWatch Logs stub source.

Conforms to the :class:`paperbark.sources.Source` Protocol so the
config layer can name it, but ``capture()`` raises until the real
implementation lands.
"""

from __future__ import annotations

from collections.abc import Iterator


class CloudWatchSource:
    name = "cloudwatch"

    def capture(self, *, since: str = "") -> Iterator[str]:
        raise NotImplementedError("cloudwatch source is not yet implemented")
