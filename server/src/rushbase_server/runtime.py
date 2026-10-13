"""Process-wide state of the running server and its background loops."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from rushbase_server.clock import Clock
from rushbase_server.db import SessionFactory
from rushbase_server.models import Session, Worker
from rushbase_server.services import backup, inprocess, queue, scanner
from rushbase_server.services.library_binding import LibraryBinding
from rushbase_server.services.ratelimit import LoginRateLimiter
from rushbase_server.services.settings_store import PIPELINE_STAGES_KEY, get_setting
from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    """Everything request handlers and background loops share."""

    settings: Settings
    engine: AsyncEngine
    session_factory: SessionFactory
    clock: Clock
    binding: LibraryBinding
    limiter: LoginRateLimiter
    pg_tools: backup.PgTools
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    @property
    def queue_config(self) -> queue.QueueConfig:
        """Queue parameters derived from the settings."""
        return queue.QueueConfig.from_settings(self.settings)

    async def enabled_stages(self) -> list[str]:
        """Pipeline stages: the admin override in ``app_setting`` or the configured default."""
        async with self.session_factory() as session:
            override = await get_setting(session, PIPELINE_STAGES_KEY)
        if isinstance(override, list):
            return [str(s) for s in override]
        return [s.value for s in self.settings.enabled_stages]

    # --- single iterations (also called directly by tests) ---

    async def scan_once(self) -> scanner.ScanReport | None:
        """One scanner pass; ``None`` when the server is not the active coordinator."""
        if not self.binding.held or self.binding.root is None:
            return None
        stages = await self.enabled_stages()
        async with self.session_factory() as session:
            try:
                report = await scanner.scan_once(
                    session,
                    self.binding.root,
                    self.clock.now(),
                    scanner.ScanConfig(self.settings.stable_scans, self.settings.stable_seconds),
                    self.queue_config,
                    stages,
                )
            except scanner.InputUnavailableError:
                logger.error("Input/ is not readable; skipping scan (share unmounted?)")
                return None
            await session.commit()
        return report

    async def maintain_once(self) -> queue.ReapReport:
        """Reaper, server-side stages, session cleanup and worker liveness."""
        now = self.clock.now()
        async with self.session_factory() as session:
            report = await queue.reap_expired(session, now, self.queue_config)
            await queue.run_server_stages(session, now)
            await session.execute(delete(Session).where(Session.expires_at < now))
            offline_before = now - timedelta(seconds=self.settings.worker_online_seconds)
            await session.execute(
                update(Worker)
                .where(Worker.status == "online", Worker.last_seen_at < offline_before)
                .values(status="offline", current_job_id=None)
            )
            await session.commit()
        if self.settings.inprocess and self.binding.root is not None:
            await inprocess.run_one(
                self.session_factory,
                self.binding.root,
                list(self.settings.inprocess),
                self.clock.now,
                self.queue_config,
            )
        return report

    async def backup_if_due(self) -> backup.BackupInfo | None:
        """Create a backup when the newest one is older than the configured interval."""
        root = self.binding.root
        if not (self.settings.backup_enabled and self.binding.held and root is not None):
            return None
        now = self.clock.now()
        directory = backup.backup_dir(root)
        if not backup.is_due(directory, now, self.settings.backup_interval_hours):
            return None
        return await self.backup_now()

    async def backup_now(self) -> backup.BackupInfo:
        """Create a backup immediately."""
        root = self.binding.root
        if root is None:
            raise backup.BackupError("no library configured")
        async with self.session_factory() as session:
            info = await backup.run_backup(
                session,
                self.pg_tools,
                make_url(self.settings.database_url.get_secret_value()),
                root,
                self.clock.now(),
                keep_interval=self.settings.backup_keep_interval,
                keep_daily=self.settings.backup_keep_daily,
            )
        logger.info("backup created", extra={"file": info.name, "bytes": info.size})
        return info

    # --- loops ---

    def start(self) -> None:
        """Start the background loops (lease heartbeat, scanner, maintenance, backups)."""
        s = self.settings
        self._spawn("lease", s.server_lease_heartbeat_seconds, self.binding.heartbeat)
        self._spawn("scanner", s.scan_interval_seconds, self._scan)
        self._spawn("maintenance", s.maintenance_interval_seconds, self._maintain)
        self._spawn("backup", 60, self.backup_if_due)

    async def _scan(self) -> None:
        await self.scan_once()

    async def _maintain(self) -> None:
        if self.binding.held:
            await self.maintain_once()

    def _spawn(self, name: str, interval: float, work: Callable[[], Awaitable[object]]) -> None:
        async def loop() -> None:
            while True:
                try:
                    await work()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("background task failed", extra={"task": name})
                await asyncio.sleep(interval)

        self.tasks.append(asyncio.create_task(loop(), name=f"rushbase-{name}"))

    async def stop(self) -> None:
        """Cancel the background loops and release the server lease."""
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()
        await self.binding.release()
