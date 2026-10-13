"""Stability logic: when is a file in ``Input/`` completely copied? (ADR-0005, decision 3)

A file is stable once its size and mtime were observed unchanged in ``required_scans``
consecutive scans (the first sighting counts) *and* for at least ``window`` seconds.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

DEFAULT_REQUIRED_SCANS: Final = 2
DEFAULT_WINDOW_SECONDS: Final = 120


@dataclass(frozen=True)
class Observation:
    """What the scanner remembers about a file between scans."""

    size: int
    mtime: datetime
    stable_count: int
    last_changed_at: datetime


@dataclass(frozen=True)
class Verdict:
    """Result of evaluating one new sighting."""

    stable_count: int
    last_changed_at: datetime
    changed: bool
    stable: bool


def evaluate(
    previous: Observation | None,
    size: int,
    mtime: datetime,
    now: datetime,
    *,
    required_scans: int = DEFAULT_REQUIRED_SCANS,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> Verdict:
    """Decide whether a file seen with ``size``/``mtime`` at ``now`` is stable.

    A changed size or mtime (or a first sighting) restarts the counter and the window.
    """
    mtime = mtime.astimezone(UTC)
    if previous is None or previous.size != size or previous.mtime.astimezone(UTC) != mtime:
        return Verdict(stable_count=1, last_changed_at=now, changed=True, stable=False)
    count = previous.stable_count + 1
    quiet = now - previous.last_changed_at >= timedelta(seconds=window_seconds)
    return Verdict(
        stable_count=count,
        last_changed_at=previous.last_changed_at,
        changed=False,
        stable=count >= required_scans and quiet,
    )
