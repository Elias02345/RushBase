"""Scheduled PostgreSQL backups onto the share, retention, JSON config export and restore.

``pg_dump``/``pg_restore`` are invoked through :class:`PgTools`: a configured binary
(``RUSHBASE_PG_DUMP`` / ``RUSHBASE_PG_RESTORE``), else the one on ``PATH``, else (development
and tests) ``docker exec`` into the compose project's PostgreSQL container
(``RUSHBASE_PG_DOCKER_CONTAINER``). Files are written to a temporary name, fsynced and renamed.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from rushbase_core.layout import BACKUPS_DIR, SYSTEM_DIR
from rushbase_core.library import atomic_write_text
from rushbase_server.models import AppSetting, FilingRule, Library
from rushbase_server.settings import Settings

STAMP_FORMAT: Final = "%Y%m%dT%H%M%SZ"
_DUMP_RE: Final = re.compile(r"^rushbase-(\d{8}T\d{6}Z)-(interval|daily)\.dump$")
_TOOL_TIMEOUT_SECONDS: Final = 3600


class BackupError(Exception):
    """A backup or restore could not be carried out."""


@dataclass(frozen=True)
class BackupInfo:
    """A dump file in the backup directory."""

    name: str
    path: Path
    size: int
    created_at: datetime
    kind: str


def backup_dir(library_root: Path) -> Path:
    """Return ``<root>/.rushbase/backups``."""
    return library_root / SYSTEM_DIR / BACKUPS_DIR


class PgTools:
    """Runs ``pg_dump`` / ``pg_restore`` directly or through ``docker exec``."""

    def __init__(
        self,
        pg_dump: str | None = None,
        pg_restore: str | None = None,
        docker_container: str | None = None,
    ) -> None:
        self._dump = pg_dump
        self._restore = pg_restore
        self._container = docker_container

    @classmethod
    def from_settings(cls, settings: Settings) -> "PgTools":
        """Build the runner from the ``RUSHBASE_PG_*`` settings."""
        return cls(settings.pg_dump, settings.pg_restore, settings.pg_docker_container)

    def _argv(self, tool: str, configured: str | None) -> tuple[list[str], bool]:
        """Return ``(argv prefix, via_docker)`` for ``tool`` or raise :class:`BackupError`."""
        binary = configured or shutil.which(tool)
        if binary:
            return [binary], False
        docker = shutil.which("docker")
        if self._container and docker:
            return [docker, "exec", "-i", self._container, tool], True
        raise BackupError(
            f"{tool} not found: install postgresql-client, set RUSHBASE_{tool.upper()} "
            "or (development only) RUSHBASE_PG_DOCKER_CONTAINER"
        )

    @staticmethod
    def _conn_args(url: URL, via_docker: bool) -> list[str]:
        args = ["-U", url.username or "postgres", "-d", url.database or "postgres"]
        if not via_docker:  # inside the container the local socket is used
            args += ["-h", url.host or "localhost", "-p", str(url.port or 5432)]
        return args

    def dump(self, url: URL, target: Path) -> None:
        """Write a custom-format dump of the database at ``url`` to ``target``."""
        argv, docker = self._argv("pg_dump", self._dump)
        argv += ["-Fc", "--no-owner", *self._conn_args(url, docker)]
        env = {**os.environ, "PGPASSWORD": url.password or ""}
        with target.open("wb") as out:
            self._run(argv, env, stdout=out)
            out.flush()
            os.fsync(out.fileno())

    def restore(self, url: URL, source: Path) -> None:
        """Restore the custom-format dump ``source`` into the database at ``url``."""
        argv, docker = self._argv("pg_restore", self._restore)
        argv += ["--no-owner", *self._conn_args(url, docker)]
        env = {**os.environ, "PGPASSWORD": url.password or ""}
        if docker:
            with source.open("rb") as stdin:
                self._run(argv, env, stdin=stdin)
        else:
            self._run([*argv, str(source)], env)

    @staticmethod
    def _run(argv: list[str], env: dict[str, str], **streams: Any) -> None:
        try:
            completed = subprocess.run(  # noqa: S603 - argv is built from trusted settings
                argv,
                env=env,
                stderr=subprocess.PIPE,
                timeout=_TOOL_TIMEOUT_SECONDS,
                check=False,
                **streams,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackupError(f"{argv[0]} failed: {exc}") from exc
        if completed.returncode != 0:
            message = completed.stderr.decode(errors="replace").strip()[-800:]
            raise BackupError(f"{Path(argv[0]).name} exited with {completed.returncode}: {message}")


def list_backups(directory: Path) -> list[BackupInfo]:
    """Return the dump files in ``directory``, newest first."""
    if not directory.is_dir():
        return []
    result: list[BackupInfo] = []
    for entry in directory.iterdir():
        match = _DUMP_RE.match(entry.name)
        if match is None or not entry.is_file():
            continue
        created = datetime.strptime(match[1], STAMP_FORMAT).replace(tzinfo=UTC)
        result.append(BackupInfo(entry.name, entry, entry.stat().st_size, created, match[2]))
    return sorted(result, key=lambda b: b.created_at, reverse=True)


def choose_kind(directory: Path, now: datetime) -> str:
    """The first backup of each UTC day is a ``daily`` one, the others ``interval``."""
    today = now.date()
    has_daily = any(
        b.kind == "daily" and b.created_at.date() == today for b in list_backups(directory)
    )
    return "interval" if has_daily else "daily"


def is_due(directory: Path, now: datetime, interval_hours: float) -> bool:
    """Whether the newest backup is older than the interval (or none exists)."""
    existing = list_backups(directory)
    return not existing or (now - existing[0].created_at).total_seconds() >= interval_hours * 3600


def prune(directory: Path, keep_interval: int, keep_daily: int) -> list[str]:
    """Delete old backups (and their config exports); returns the removed dump names."""
    removed: list[str] = []
    for kind, keep in (("interval", keep_interval), ("daily", keep_daily)):
        for old in [b for b in list_backups(directory) if b.kind == kind][keep:]:
            old.path.unlink(missing_ok=True)
            stamp = old.name.split("-")[1]
            (directory / f"rushbase-{stamp}-{old.kind}-config.json").unlink(missing_ok=True)
            removed.append(old.name)
    return removed


async def export_config(session: AsyncSession) -> dict[str, Any]:
    """Return settings, filing rules and library info as a JSON-serialisable dict."""
    settings = (await session.execute(select(AppSetting).order_by(AppSetting.key))).scalars()
    rules = (await session.execute(select(FilingRule).order_by(FilingRule.version))).scalars()
    library = (await session.execute(select(Library))).scalars().first()
    revision = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "schema_revision": revision,
        "library": None
        if library is None
        else {"id": str(library.id), "name": library.name, "settings": library.settings},
        "app_settings": {s.key: s.value for s in settings},
        "filing_rules": [
            {
                "version": r.version,
                "template": r.template,
                "fallbacks": r.fallbacks,
                "active": r.active,
                "note": r.note,
            }
            for r in rules
        ],
    }


async def run_backup(
    session: AsyncSession,
    tools: PgTools,
    url: URL,
    library_root: Path,
    now: datetime,
    *,
    keep_interval: int = 8,
    keep_daily: int = 14,
) -> BackupInfo:
    """Create a dump plus config export in ``.rushbase/backups/`` and prune old ones.

    Raises:
        BackupError: the dump failed; no partial file is left behind.
    """
    directory = backup_dir(library_root)
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    kind = await asyncio.to_thread(choose_kind, directory, now)
    stamp = now.strftime(STAMP_FORMAT)
    name = f"rushbase-{stamp}-{kind}.dump"
    tmp = directory / f".{name}.{secrets.token_hex(4)}.rbtmp"
    try:
        await asyncio.to_thread(tools.dump, url, tmp)
        await asyncio.to_thread(os.replace, tmp, directory / name)
    finally:
        tmp.unlink(missing_ok=True)
    config = await export_config(session)
    config_path = directory / f"rushbase-{stamp}-{kind}-config.json"
    await asyncio.to_thread(atomic_write_text, config_path, json.dumps(config, indent=2) + "\n")
    await asyncio.to_thread(prune, directory, keep_interval, keep_daily)
    path = directory / name
    return BackupInfo(name, path, path.stat().st_size, now.replace(microsecond=0), kind)


async def database_is_empty(engine: AsyncEngine) -> bool:
    """Whether the database holds no users, batches or assets (fresh or migrated-only)."""
    async with engine.connect() as connection:
        for table in ("user", "batch", "asset"):
            exists = await connection.scalar(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f'public."{table}"'}
            )
            if not exists:
                continue
            query = text(f'SELECT EXISTS (SELECT 1 FROM "{table}")')  # noqa: S608 - fixed names
            if await connection.scalar(query):
                return False
    return True


async def restore_backup(engine: AsyncEngine, tools: PgTools, url: URL, dump: Path) -> None:
    """Restore ``dump`` into an empty database (replaces the migrated-but-empty schema).

    Raises:
        BackupError: the database already contains data, or ``pg_restore`` failed.
    """
    if not await asyncio.to_thread(dump.is_file):
        raise BackupError(f"backup file not found: {dump}")
    if not await database_is_empty(engine):
        raise BackupError("refusing to restore: the target database is not empty")
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text("DROP SCHEMA public CASCADE"))
        await autocommit.execute(text("CREATE SCHEMA public"))
    await asyncio.to_thread(tools.restore, url, dump)
