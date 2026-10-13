"""Binds the server to the library on the share and holds the server lease."""

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from rushbase_core import (
    LibraryError,
    LibraryManifest,
    ServerLeaseLostError,
    acquire_server_lease,
    init_library,
    open_library,
    release_server_lease,
    renew_server_lease,
)
from rushbase_server.clock import Clock
from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)


class LibraryBinding:
    """Tracks the mounted library and the server lease of this process.

    ``root`` is ``None`` when no library is configured. ``mounted`` and ``held`` are refreshed
    by :meth:`heartbeat` so health checks never touch the share themselves.
    """

    def __init__(self, settings: Settings, clock: Clock) -> None:
        self.root: Path | None = settings.library_root
        self.holder = settings.holder_id
        self._ttl = settings.server_lease_seconds
        self._clock = clock
        self.manifest: LibraryManifest | None = None
        self.mounted = False
        self.held = False

    @property
    def library_id(self) -> UUID | None:
        """Id of the bound library, if any."""
        return self.manifest.library_id if self.manifest else None

    def initialised(self) -> bool:
        """Whether ``library.json`` exists below the configured root."""
        if self.root is None:
            return False
        try:
            open_library(self.root)
        except LibraryError:
            return False
        return True

    async def bind(self, *, force: bool = False) -> bool:
        """Open the library and take the server lease.

        Returns ``False`` when no library is configured or it is not initialised yet (the
        setup wizard initialises it and calls :meth:`bind_after_setup`).

        Raises:
            ServerLeaseHeldError: another live server owns the library and ``force`` is false.
            LibraryError: the manifest is unreadable or from a newer release.
        """
        if self.root is None:
            return False
        root = self.root
        if not await asyncio.to_thread(self.initialised):
            return False
        self.manifest = await asyncio.to_thread(open_library, root)
        await asyncio.to_thread(
            acquire_server_lease,
            root,
            self.holder,
            ttl_seconds=self._ttl,
            now=self._clock.now(),
            force=force,
        )
        self.mounted = True
        self.held = True
        logger.info("library bound", extra={"library_id": str(self.manifest.library_id)})
        return True

    async def init_and_bind(self) -> LibraryManifest:
        """Create (or adopt) the library layout, then bind. Used by the setup endpoint."""
        if self.root is None:
            raise LibraryError("RUSHBASE_LIBRARY_ROOT is not configured")
        await asyncio.to_thread(init_library, self.root)
        await self.bind(force=False)
        assert self.manifest is not None  # bind() succeeded after init_library
        return self.manifest

    async def heartbeat(self) -> None:
        """Renew the lease and refresh the mount state; marks the lease lost on takeover."""
        if self.root is None or not self.held:
            return
        try:
            await asyncio.to_thread(
                renew_server_lease,
                self.root,
                self.holder,
                ttl_seconds=self._ttl,
                now=self._clock.now(),
            )
            self.mounted = True
        except ServerLeaseLostError:
            self.held = False
            logger.error("server lease lost: another server took over the library")
        except LibraryError:
            self.mounted = False
            logger.error("library not readable (share unmounted?)", exc_info=True)

    async def release(self) -> None:
        """Give the lease back on graceful shutdown."""
        if self.root is None or not self.held:
            return
        try:
            await asyncio.to_thread(release_server_lease, self.root, self.holder)
        except LibraryError:
            logger.warning("could not release the server lease", exc_info=True)
        self.held = False
