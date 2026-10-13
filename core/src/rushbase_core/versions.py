"""Comparison of model versions for re-processing ("recompute stage X below version Y")."""

import re

_PART = re.compile(r"\d+|[A-Za-z]+")


def version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Return a sortable key for a version string such as ``"2.1"`` or ``"2024-06-01"``.

    Numeric parts compare numerically and sort before alphabetic parts, so ``"2" < "10"`` and
    ``"1.0" < "1.0rc1"`` is *not* assumed (pre-release tags are just later text parts).
    """
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower()) for part in _PART.findall(version)
    )


def version_lt(left: str, right: str) -> bool:
    """Return whether version ``left`` is older than ``right``."""
    return version_key(left) < version_key(right)
