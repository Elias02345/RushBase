"""Rushbase worker engine and ``rushbase`` command line interface."""

from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    """Return the installed ``rushbase-worker`` version."""
    try:
        return version("rushbase-worker")
    except PackageNotFoundError:  # pragma: no cover - only when run from an unbuilt tree
        return "0+unknown"
