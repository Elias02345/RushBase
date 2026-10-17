"""A tiny in-process TTL cache for expensive read-only aggregates (facets, statistics).

Concurrent requests for the same key share one computation, so a burst of identical dashboard
loads costs a single query. Entries expire after ``ttl`` seconds; ``ttl == 0`` disables caching
(tests, and operators who want exact numbers at any price). With ``stale=True`` an expired value
is still served while one background refresh recomputes it, so nobody waits for a slow aggregate
after the first load.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

logger = logging.getLogger(__name__)


class TtlCache:
    """Async get-or-compute cache with a size bound, request coalescing and optional SWR."""

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
        self._refreshing: set[asyncio.Task[None]] = set()
        self._scheduled: set[Hashable] = set()

    async def get_or_compute[T](
        self, key: Hashable, compute: Callable[[], Awaitable[T]], *, stale: bool = False
    ) -> T:
        """Return the cached value for ``key`` or run ``compute`` (once for concurrent callers).

        ``compute`` must not depend on the caller's request (open your own database session): with
        ``stale=True`` it may run in the background after the caller has been answered.
        """
        if self._ttl <= 0:
            return await compute()
        hit = self._done.get(key)
        if hit is not None:
            self._done.move_to_end(key)
            if hit[0] > self._clock():
                value: T = hit[1]
                return value
            if stale:
                if key not in self._running and key not in self._scheduled:
                    self._scheduled.add(key)
                    task = asyncio.create_task(self._refresh(key, compute))
                    self._refreshing.add(task)
                    task.add_done_callback(self._refreshing.discard)
                old: T = hit[1]
                return old
        return await self._run(key, compute)

    async def _refresh(self, key: Hashable, compute: Callable[[], Awaitable[Any]]) -> None:
        try:
            await self._run(key, compute)
        except Exception:
            logger.warning("background refresh of a cached value failed", exc_info=True)
        finally:
            self._scheduled.discard(key)

    async def _run[T](self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
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
            self._done.move_to_end(key)
            while len(self._done) > self._max:
                self._done.popitem(last=False)
            return result
        finally:
            del self._running[key]

    def clear(self) -> None:
        """Drop all cached values."""
        self._done.clear()
