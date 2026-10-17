"""Process-wide state of the running server and its background loops."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from rushbase_server.clock import Clock
from rushbase_server.db import SessionFactory
from rushbase_server.models import Session, Worker
from rushbase_server.search.encoder import NullEncoder, QueryEncoder
from rushbase_server.services import (
    backup,
    filing,
    inprocess,
    needs,
    quarantine,
    queue,
    scanner,
)
from rushbase_server.services.cache import TtlCache
from rushbase_server.services.library_binding import LibraryBinding
from rushbase_server.services.ratelimit import LoginRateLimiter, TokenBuckets
from rushbase_server.services.settings_store import (
    CENTRAL_META_KEY,
    PIPELINE_STAGES_KEY,
    get_setting,
)
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
    registrations: LoginRateLimiter
    pg_tools: backup.PgTools
    encoder: QueryEncoder = field(default_factory=NullEncoder)
    buckets: TokenBuckets = field(default_factory=TokenBuckets)
    cache: TtlCache = field(default_factory=lambda: TtlCache(0))
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

    async def filing_env(self) -> filing.FilingEnv | None:
        """Settings of the filing service; ``None`` when no library is mounted."""
        if self.binding.root is None or not self.binding.mounted:
            return None
        async with self.session_factory() as session:
            central = bool(await get_setting(session, CENTRAL_META_KEY, False))
        return filing.FilingEnv(
            root=self.binding.root,
            library_id=self.binding.library_id,
            path_budget=self.settings.filing_path_budget,
            central_meta=central,
            queue_config=self.queue_config,
        )

    async def cached[T](self, key: Hashable, compute: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """Serve an expensive aggregate from the TTL cache (own session, stale while refreshing)."""

        async def run() -> T:
            async with self.session_factory() as session:
                return await compute(session)

        return await self.cache.get_or_compute(key, run, stale=True)

    # --- single iterations (also called directly by tests) ---

    async def scan_once(self) -> scanner.ScanReport | None:
        """One scanner pass; ``None`` when the server is not the active coordinator."""
        if not self.binding.held or not self.binding.mounted or self.binding.root is None:
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
        """Reaper, quarantine, finalize (filing), session cleanup and worker liveness."""
        now = self.clock.now()
        async with self.session_factory() as session:
            report = await queue.reap_expired(session, now, self.queue_config)
            await session.execute(delete(Session).where(Session.expires_at < now))
            offline_before = now - timedelta(seconds=self.settings.worker_online_seconds)
            await session.execute(
                update(Worker)
                .where(Worker.status == "online", Worker.last_seen_at < offline_before)
                .values(status="offline", current_job_id=None)
            )
            await session.commit()
        env = await self.filing_env()
        if env is not None:
            await quarantine.process_pending(self.session_factory, env.root, self.clock.now)
            await filing.run_finalize(
                self.session_factory, env, self.clock.now, on_filed=self.match_needs
            )
        if self.settings.inprocess and self.binding.root is not None and self.binding.mounted:
            await inprocess.run_one(
                self.session_factory,
                self.binding.root,
                list(self.settings.inprocess),
                self.clock.now,
                self.queue_config,
            )
        return report

    async def match_needs(self, asset_id: UUID) -> None:
        """Attach a filed or re-classified asset to matching open needs items (passive)."""
        try:
            async with self.session_factory() as session:
                await needs.match_asset(
                    session,
                    self.encoder,
                    asset_id,
                    self.settings.needs_match_threshold,
                    self.cache,
                )
                await session.commit()
        except Exception:
            logger.warning("needs matching failed", extra={"asset": str(asset_id)}, exc_info=True)

    async def backup_if_due(self) -> backup.BackupInfo | None:
        """Create a backup when the newest one is older than the configured interval."""
        root = self.binding.root
        mounted = self.binding.mounted
        if not (
            self.settings.backup_enabled and self.binding.held and mounted and root is not None
        ):
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

    def warm_encoder(self) -> None:
        """Load the query encoders in a worker thread so the first search is not slow."""

        async def warm() -> None:
            try:
                await asyncio.to_thread(self.encoder.warm)
            except Exception:
                logger.warning("query encoder warm-up failed", exc_info=True)

        self.tasks.append(asyncio.create_task(warm(), name="rushbase-encoder-warmup"))

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
