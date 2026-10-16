"""GPU scheduling: at most one GPU-resident model family at a time (ADR-0006 decision 6).

On cards with 8 GB or less the VLM and Whisper never share the GPU. Stage handlers acquire a
named resource (``"vlm"``, ``"whisper"``, ...) through :meth:`ModelLifecycle.use`. Handlers of
the same resource share it concurrently; a different resource waits until the current one has no
users, which then unloads it. Because the server claims jobs stage-batched, switches are rare.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class ModelLifecycle:
    """Loads a resource on first use and unloads it when another GPU resource is requested."""

    def __init__(self, *, exclusive: bool = True) -> None:
        self._exclusive = exclusive
        self._condition = asyncio.Condition()
        self._current: str | None = None
        self._users = 0
        self._resources: dict[str, Any] = {}
        self._closers: dict[str, Callable[[Any], Awaitable[None]]] = {}
        self._gpu: set[str] = set()
        self.history: list[str] = []
        """Order of loads and unloads (``load:vlm``, ``unload:vlm``), for tests and logs."""

    @property
    def loaded(self) -> list[str]:
        return list(self._resources)

    @contextlib.asynccontextmanager
    async def use(
        self,
        name: str,
        loader: Callable[[], Awaitable[Any]],
        closer: Callable[[Any], Awaitable[None]],
        *,
        gpu: bool = True,
    ) -> AsyncIterator[Any]:
        """Hold ``name`` loaded for the duration of the block (``gpu=False`` skips exclusivity)."""
        if not (gpu and self._exclusive):
            resource = await self._load_shared(name, loader, closer)
            yield resource
            return
        async with self._condition:
            await self._condition.wait_for(lambda: self._current in {None, name})
            for other in [n for n in self._gpu if n != name]:
                await self._unload(other)
            if name not in self._resources:
                self._resources[name] = await loader()
                self._closers[name] = closer
                self._gpu.add(name)
                self.history.append(f"load:{name}")
            self._current = name
            self._users += 1
            resource = self._resources[name]
        try:
            yield resource
        finally:
            async with self._condition:
                self._users -= 1
                if self._users == 0:
                    self._current = None
                self._condition.notify_all()

    async def _load_shared(
        self,
        name: str,
        loader: Callable[[], Awaitable[Any]],
        closer: Callable[[Any], Awaitable[None]],
    ) -> Any:
        async with self._condition:
            if name not in self._resources:
                self._resources[name] = await loader()
                self._closers[name] = closer
                self.history.append(f"load:{name}")
            return self._resources[name]

    async def unload_idle(self, *, keep: str | None = None) -> None:
        """Unload every resource that has no users (called when the queue is empty)."""
        async with self._condition:
            if self._users:
                return
            for name in [n for n in self._resources if n != keep]:
                await self._unload(name)

    async def close(self) -> None:
        """Unload everything (worker shutdown)."""
        async with self._condition:
            for name in list(self._resources):
                await self._unload(name)

    async def _unload(self, name: str) -> None:
        resource = self._resources.pop(name)
        self._gpu.discard(name)
        closer = self._closers.pop(name)
        with contextlib.suppress(Exception):
            await closer(resource)
        self.history.append(f"unload:{name}")
