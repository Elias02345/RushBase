"""In-memory sliding-window limiter for failed logins (single server instance)."""

from collections import defaultdict, deque
from datetime import datetime, timedelta

from rushbase_server.clock import Clock


class LoginRateLimiter:
    """Blocks a key after ``max_failures`` failures inside ``window_seconds``."""

    def __init__(self, clock: Clock, max_failures: int, window_seconds: float) -> None:
        self._clock = clock
        self._max = max_failures
        self._window = timedelta(seconds=window_seconds)
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[datetime]:
        failures = self._failures[key]
        cutoff = self._clock.now() - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            self._failures.pop(key, None)
        return failures

    def retry_after(self, key: str) -> int:
        """Return seconds until ``key`` may try again (0 when it is not blocked)."""
        failures = self._prune(key)
        if len(failures) < self._max:
            return 0
        remaining = failures[0] + self._window - self._clock.now()
        return max(1, int(remaining.total_seconds()) + 1)

    def register_failure(self, key: str) -> None:
        """Record one failed attempt for ``key``."""
        self._prune(key)
        self._failures[key].append(self._clock.now())

    def reset(self, key: str) -> None:
        """Forget all failures of ``key`` (after a successful login)."""
        self._failures.pop(key, None)
