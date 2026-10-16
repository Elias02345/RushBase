import shutil
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from rushbase_core import (
    LibraryMismatchError,
    ServerLeaseHeldError,
    acquire_server_lease,
    init_library,
    open_library,
)
from rushbase_server.app import create_app
from rushbase_server.models import User
from rushbase_server.services import backup
from support import Server, make_settings, migrate, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


def need_pg_tools() -> None:
    if not (shutil.which("pg_dump") or shutil.which("docker")):
        pytest.skip("neither pg_dump nor docker available")


async def snapshot(url: str) -> dict[str, set[str]]:
    engine = create_async_engine(url)
    result: dict[str, set[str]] = {}
    try:
        async with engine.connect() as connection:
            for table in ("user", "asset", "asset_file", "batch", "job"):
                query = text(f'SELECT id FROM "{table}"')  # noqa: S608 - fixed table names
                result[table] = {str(v) for v in (await connection.execute(query)).scalars()}
    finally:
        await engine.dispose()
    return result


async def test_backup_drop_restore_gives_identical_data(
    server_factory: ServerFactory, database_url: str, fresh_database_url: str, library_root: Path
) -> None:
    need_pg_tools()
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        await seed_asset(server.runtime.session_factory)
        await seed_asset(server.runtime.session_factory)
        info = await server.runtime.backup_now()
        assert info.path.parent == library_root / ".rushbase" / "backups"
        assert info.path.is_file()
        assert info.kind == "daily"
        assert list(info.path.parent.glob("*.rbtmp")) == []  # temp+rename: nothing left behind
        config = info.path.with_name(info.path.name.replace(".dump", "-config.json"))
        assert config.is_file()
        assert '"app_settings"' in config.read_text()
        before = await snapshot(database_url)
        assert len(before["asset"]) == 2
        assert before["user"]

        # restoring over live data is refused
        engine = create_async_engine(database_url)
        try:
            with pytest.raises(backup.BackupError, match="not empty"):
                await backup.restore_backup(
                    engine, server.runtime.pg_tools, make_url(database_url), info.path
                )
        finally:
            await engine.dispose()

    # "drop the database": restore into a brand-new one (no schema at all)
    engine = create_async_engine(fresh_database_url)
    try:
        await backup.restore_backup(
            engine,
            backup.PgTools.from_settings(make_settings(fresh_database_url)),
            make_url(fresh_database_url),
            info.path,
        )
    finally:
        await engine.dispose()
    assert await snapshot(fresh_database_url) == before
    migrate(fresh_database_url)  # already at head: no-op, proves alembic_version was restored


async def test_restore_works_on_a_migrated_but_empty_database_and_server_runs_on_it(
    server_factory: ServerFactory, database_url: str, fresh_database_url: str, library_root: Path
) -> None:
    need_pg_tools()
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        info = await server.runtime.backup_now()
    migrate(fresh_database_url)  # a replacement server migrated its empty database first
    async with server_factory(fresh_database_url, library_root=library_root) as replacement:
        offered = (await replacement.client.get("/api/v1/setup")).json()
        assert offered["needs_setup"] is True
        assert offered["restore_available"] is True
        assert offered["backups"][0]["name"] == info.name
    engine = create_async_engine(fresh_database_url)
    try:
        await backup.restore_backup(
            engine,
            backup.PgTools.from_settings(make_settings(fresh_database_url)),
            make_url(fresh_database_url),
            info.path,
        )
    finally:
        await engine.dispose()
    async with server_factory(fresh_database_url, library_root=library_root) as restored:
        assert (await restored.client.get("/api/v1/setup")).json()["needs_setup"] is False
        login = await restored.client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "correct horse battery"}
        )
        assert login.status_code == 200


def test_retention_keeps_newest_per_kind_and_removes_config_exports(tmp_path: Path) -> None:
    directory = tmp_path
    base = datetime(2026, 10, 1, tzinfo=UTC)
    for hours in range(0, 60, 6):
        stamp = (base + timedelta(hours=hours)).strftime(backup.STAMP_FORMAT)
        kind = "daily" if hours % 24 == 0 else "interval"
        (directory / f"rushbase-{stamp}-{kind}.dump").write_bytes(b"x")
        (directory / f"rushbase-{stamp}-{kind}-config.json").write_text("{}")
    (directory / "unrelated.txt").write_text("keep")
    removed = backup.prune(directory, keep_interval=3, keep_daily=1)
    names = {b.name for b in backup.list_backups(directory)}
    assert len(names) == 4
    assert len(removed) == 10 - 4
    assert (directory / "unrelated.txt").exists()
    assert len(list(directory.glob("*-config.json"))) == 4
    newest = backup.list_backups(directory)[0]
    assert newest.created_at == base + timedelta(hours=54)
    assert not backup.is_due(directory, newest.created_at + timedelta(hours=5), 6)
    assert backup.is_due(directory, newest.created_at + timedelta(hours=6), 6)
    assert backup.choose_kind(directory, datetime(2026, 10, 3, 9, tzinfo=UTC)) == "interval"
    assert backup.choose_kind(directory, datetime(2026, 10, 9, 9, tzinfo=UTC)) == "daily"


def test_missing_pg_tools_give_an_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    tools = backup.PgTools(None, None, None)
    with pytest.raises(backup.BackupError, match="RUSHBASE_PG_DUMP"):
        tools.dump(make_url("postgresql://u@h/db"), tmp_path / "x")


# --- server lease -------------------------------------------------------------------------


async def test_second_server_refuses_to_start_while_the_lease_is_live(
    database_url: str, library_root: Path
) -> None:
    acquire_server_lease(library_root, "someone-else", ttl_seconds=600)
    app = create_app(make_settings(database_url, library_root=library_root))
    with pytest.raises(ServerLeaseHeldError, match="someone-else"):
        async with app.router.lifespan_context(app):
            pytest.fail("must not start")


async def test_force_takeover_and_graceful_release(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    acquire_server_lease(library_root, "someone-else", ttl_seconds=600)
    async with server_factory(
        database_url, library_root=library_root, force_takeover=True, holder_id="me"
    ) as server:
        lease = open_library(library_root).server_lease
        assert lease is not None
        assert lease.holder == "me"
        assert server.runtime.binding.held
    assert open_library(library_root).server_lease is None  # released on shutdown


async def test_expired_foreign_lease_is_taken_over_and_lost_lease_degrades_health(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    acquire_server_lease(library_root, "ghost", ttl_seconds=1, now=datetime(2020, 1, 1, tzinfo=UTC))
    async with server_factory(database_url, library_root=library_root, holder_id="me") as server:
        await server.setup_admin()
        assert (await server.client.get("/health")).status_code == 200
        # someone forces a takeover while this server is running
        acquire_server_lease(library_root, "usurper", ttl_seconds=600, force=True)
        await server.runtime.binding.heartbeat()
        assert not server.runtime.binding.held
        health = await server.client.get("/health")
        assert health.status_code == 503
        assert health.json()["lease_held"] is False
        assert await server.runtime.scan_once() is None  # no longer the active coordinator
        claim = await server.client.post("/api/v1/worker/claim", json={})
        assert claim.status_code == 401  # auth first; coordinators check happens after
    assert open_library(library_root).server_lease is not None  # the usurper's lease untouched


async def test_wrong_library_for_the_database_is_refused(
    server_factory: ServerFactory, database_url: str, library_root: Path, tmp_path: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        async with server.runtime.session_factory() as session:
            assert (await session.execute(select(User))).scalars().first() is not None
    other = tmp_path / "other-library"
    init_library(other)
    app = create_app(make_settings(database_url, library_root=other))
    with pytest.raises(LibraryMismatchError):
        async with app.router.lifespan_context(app):
            pytest.fail("must not start")
    assert open_library(other).server_lease is None  # lease handed back after the mismatch


async def test_setup_restore_endpoint_restores_a_backup_on_an_empty_database(
    server_factory: ServerFactory, database_url: str, fresh_database_url: str, library_root: Path
) -> None:
    need_pg_tools()
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        info = await server.runtime.backup_now()
    migrate(fresh_database_url)
    async with server_factory(fresh_database_url, library_root=library_root) as replacement:
        client = replacement.client
        missing = await client.post("/api/v1/setup/restore", json={"backup_name": "nope.dump"})
        assert missing.status_code == 404
        traversal = await client.post("/api/v1/setup/restore", json={"backup_name": "../x"})
        assert traversal.status_code == 404
        restored = await client.post("/api/v1/setup/restore", json={"backup_name": info.name})
        assert restored.status_code == 200, restored.text
        assert (await client.get("/api/v1/setup")).json()["needs_setup"] is False
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "correct horse battery"}
        )
        assert login.status_code == 200
        again = await client.post("/api/v1/setup/restore", json={"backup_name": info.name})
        assert again.status_code == 409
