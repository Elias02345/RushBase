"""Rushbase coordinator server."""

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    """Return the installed ``rushbase-server`` version."""
    try:
        return version("rushbase-server")
    except PackageNotFoundError:  # pragma: no cover - only when run from an unbuilt tree
        return "0+unknown"
