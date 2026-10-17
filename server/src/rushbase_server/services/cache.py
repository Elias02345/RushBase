"""A tiny in-process TTL cache for expensive read-only aggregates (facets, statistics).

Concurrent requests for the same key share one computation, so a burst of identical dashboard
loads costs a single query. Entries expire after ``ttl`` seconds; ``ttl == 0`` disables caching
(tests, and operators who want exact numbers at any price).
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class TtlCache:
    """Async get-or-compute cache with a size bound and request coalescing."""

    def __init__(
        self,
        ttl: float,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._clock = clock
        self._done: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._running: dict[Hashable, asyncio.Future[Any]] = {}

    async def get_or_compute[T](self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value for ``key`` or run ``compute`` (once for concurrent callers)."""
        if self._ttl <= 0:
            return await compute()
        now = self._clock()
        hit = self._done.get(key)
        if hit is not None and hit[0] > now:
            self._done.move_to_end(key)
            value: T = hit[1]
            return value
        pending = self._running.get(key)
        if pending is not None:
            shared: T = await asyncio.shield(pending)
            return shared
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._running[key] = future
        try:
            result = await compute()
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark as retrieved when nobody else waits
            raise
        else:
            future.set_result(result)
            self._done[key] = (self._clock() + self._ttl, result)
            while len(self._done) > self._max:
                self._done.popitem(last=False)
            return result
        finally:
            del self._running[key]

    def clear(self) -> None:
        """Drop all cached values."""
        self._done.clear()
