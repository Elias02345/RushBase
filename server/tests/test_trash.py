from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from pipeline import enroll, finalize_all, put_file, run_workers, scan
from rushbase_server.models import Asset, AssetFile, AuditLog, TrashItem
from rushbase_server.services import trash
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def filed(server: Server, admin: dict[str, str], root: Path) -> str:
    put_file(root, "Input/Harbour/clip.mov", b"video-bytes")
    await scan(server)
    worker = await enroll(server, admin)
    await run_workers(server, worker, root)
    await finalize_all(server)
    items = (await server.client.get("/api/v1/assets")).json()["items"]
    return str(items[0]["id"])


async def test_trash_restore_and_conflict(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        asset_id = await filed(server, admin, library_root)
        detail = (await client.get(f"/api/v1/assets/{asset_id}")).json()
        original = detail["files"][0]["rel_path"]
        sidecar = list((library_root / Path(original).parent).glob("_meta/*.json"))
        assert (library_root / original).is_file()
        assert sidecar

        url = f"/api/v1/assets/{asset_id}/trash"
        assert (await client.post(url, json={"confirm": False}, headers=admin)).status_code == 422
        assert (await client.post(url, json={"confirm": True}, headers=admin)).status_code == 204

        trashed = library_root / ".rushbase/trash" / asset_id
        assert not (library_root / original).exists()
        assert not sidecar[0].exists()
        assert (trashed / original).is_file()
        assert (trashed / "manifest.json").is_file()
        listing = (await client.get("/api/v1/admin/trash")).json()
        assert listing["retention_days"] == 30
        assert [i["asset_id"] for i in listing["items"]] == [asset_id]
        assert (await client.get("/api/v1/assets")).json()["total"] == 0
        # second trash of a trashed asset is refused
        assert (await client.post(url, json={"confirm": True}, headers=admin)).status_code == 409

        # restore conflicts when the original path is occupied; nothing moves
        put_file(library_root, original, b"someone else")
        refused = await client.post(f"/api/v1/admin/trash/{asset_id}/restore", headers=admin)
        assert refused.status_code == 409
        assert original in refused.text
        assert (trashed / original).is_file()
        (library_root / original).unlink()

        ok = await client.post(f"/api/v1/admin/trash/{asset_id}/restore", headers=admin)
        assert ok.status_code == 204
        assert (library_root / original).read_bytes() == b"video-bytes"
        assert sidecar[0].is_file()
        assert not trashed.exists()
        assert (await client.get(f"/api/v1/assets/{asset_id}")).json()["status"] == "filed"
        async with server.runtime.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(TrashItem)) == 0
            actions = set((await session.execute(select(AuditLog.action))).scalars())
        assert {"asset.trash", "asset.trash_restore"} <= actions


async def test_trash_is_admin_and_session_only(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        asset_id = await filed(server, admin, library_root)
        token = (
            await server.client.post("/api/v1/tokens", json={"name": "t"}, headers=admin)
        ).json()["token"]
        server.client.cookies.clear()
        bearer = {"Authorization": f"Bearer {token}"}
        url = f"/api/v1/assets/{asset_id}/trash"
        denied = await server.client.post(url, json={"confirm": True}, headers=bearer)
        assert denied.status_code == 403
        assert (await server.client.get("/api/v1/admin/trash", headers=bearer)).status_code == 403


async def test_purge_deletes_only_expired_trash(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        asset_id = await filed(server, admin, library_root)
        original = (await client.get(f"/api/v1/assets/{asset_id}")).json()["files"][0]["rel_path"]
        trashed = await client.post(
            f"/api/v1/assets/{asset_id}/trash", json={"confirm": True}, headers=admin
        )
        assert trashed.status_code == 204, trashed.text
        put_file(library_root, "Content/keep/other.mov", b"unrelated")
        factory, now = server.runtime.session_factory, server.clock.now

        saved = await client.put(
            "/api/v1/admin/library-settings",
            json={"path_roots": {}, "central_meta": False, "trash_retention_days": 10},
            headers=admin,
        )
        assert saved.status_code == 200, saved.text
        server.clock.advance(9 * 86400)
        assert await trash.purge_expired(factory, library_root, now) == 0
        assert (library_root / ".rushbase/trash" / asset_id / original).is_file()
        server.clock.advance(2 * 86400)
        assert await trash.purge_expired(factory, library_root, now) == 1
        assert not (library_root / ".rushbase/trash" / asset_id).exists()
        assert (library_root / "Content/keep/other.mov").read_bytes() == b"unrelated"
        async with factory() as session:
            assert await session.get(Asset, asset_id) is None
            assert not (
                await session.execute(select(AssetFile).where(AssetFile.asset_id == asset_id))
            ).all()
            purge = (
                await session.execute(select(AuditLog).where(AuditLog.action == "asset.purge"))
            ).scalar_one()
            assert purge.target_id == asset_id


def test_purge_refuses_paths_outside_the_trash(tmp_path: Path) -> None:
    (tmp_path / "Content").mkdir()
    (tmp_path / "Content/a.mov").write_bytes(b"x")
    for bad in ("Content", "Content/a.mov", ".rushbase/trash/../../Content", ".rushbase/journal"):
        with pytest.raises(ValueError, match="refusing"):
            trash._remove_tree(tmp_path, bad)
    assert (tmp_path / "Content/a.mov").is_file()


def test_purge_refuses_a_trash_folder_replaced_by_a_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "Content" / "victim"
    victim.mkdir(parents=True)
    (victim / "a.mov").write_bytes(b"x")
    (tmp_path / ".rushbase").mkdir()
    (tmp_path / ".rushbase/trash").symlink_to(tmp_path / "Content", target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        trash._remove_tree(tmp_path, ".rushbase/trash/victim")
    assert (victim / "a.mov").is_file()
