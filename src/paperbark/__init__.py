"""Paperbark: configurable cross-source log capture, search, and analysis CLI."""

from importlib.metadata import PackageNotFoundError, version


def _resolve_version() -> str:
    try:
        return version("paperbark")
    except PackageNotFoundError:
        # Editable / source checkout without installed metadata. Keep a
        # readable sentinel rather than crashing imports during development.
        return "0.0.0+unknown"


__version__ = _resolve_version()

__all__ = ["__version__"]
