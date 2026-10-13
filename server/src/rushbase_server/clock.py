"""Injectable clock so lease expiry, backoff and rate limits are testable without sleeping."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of the current time (timezone-aware, UTC)."""

    def now(self) -> datetime:
        """Return the current time."""
        ...


class SystemClock:
    """The wall clock."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(UTC)


class ManualClock:
    """A clock that only moves when told to (tests, simulations)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the frozen time."""
        return self._now

    def advance(self, seconds: float) -> datetime:
        """Move time forward by ``seconds`` and return the new time."""
        self._now += timedelta(seconds=seconds)
        return self._now
