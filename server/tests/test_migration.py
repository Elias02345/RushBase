from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import select

from pipeline import enroll, finalize_all, put_file, run_workers, scan
from rushbase_core import mcp_tools as spec
from rushbase_server.models import MoveJournal
from rushbase_server.services import migration
from support import Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]
NEW_TEMPLATE = "Content/by-batch/{batch_label|Unsorted}/"


def test_mcp_has_no_migration_or_trash_tool() -> None:
    names = " ".join(tool.name for tool in spec.TOOLS)
    for word in ("migrat", "trash", "lock", "relink"):
        assert word not in names


def test_confirmation_text() -> None:
    assert migration.expected_confirmation(3) == "MIGRATE 3"


async def test_migration_is_off_by_default_and_needs_typed_confirmation(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        put_file(library_root, "Input/Harbour/clip.mov", b"video-bytes")
        await scan(server)
        await run_workers(server, await enroll(server, admin), library_root)
        await finalize_all(server)
        detail = (await client.get("/api/v1/assets")).json()["items"][0]
        asset_id = detail["id"]
        old = (await client.get(f"/api/v1/assets/{asset_id}")).json()["files"][0]["rel_path"]
        body = {"asset_ids": [asset_id], "template": NEW_TEMPLATE}

        assert (await client.get("/api/v1/admin/migration/settings")).json() == {"enabled": False}
        disabled = await client.post("/api/v1/admin/migration/preview", json=body, headers=admin)
        assert disabled.status_code == 403

        await client.put(
            "/api/v1/admin/library-settings",
            json={"path_roots": {"linux": "/mnt/lib"}, "central_meta": False},
            headers=admin,
        )
        on = await client.put(
            "/api/v1/admin/migration/settings", json={"enabled": True}, headers=admin
        )
        assert on.json() == {"enabled": True}
        preview = (
            await client.post("/api/v1/admin/migration/preview", json=body, headers=admin)
        ).json()
        assert preview["to_move"] == 1
        assert preview["confirmation"] == "MIGRATE 1"
        assert preview["items"][0]["new_dir"] == "Content/by-batch/Harbour"
        assert (library_root / old).is_file()  # a preview moves nothing

        wrong = await client.post(
            "/api/v1/admin/migration/execute",
            json={**body, "confirmation": "yes"},
            headers=admin,
        )
        assert wrong.status_code == 422
        assert (library_root / old).is_file()

        done = await client.post(
            "/api/v1/admin/migration/execute",
            json={**body, "confirmation": "MIGRATE 1"},
            headers=admin,
        )
        assert done.status_code == 200, done.text
        result = done.json()
        assert result["moved"] == 1
        new = (await client.get(f"/api/v1/assets/{asset_id}")).json()["files"][0]["rel_path"]
        assert new.startswith("Content/by-batch/Harbour/")
        assert not (library_root / old).exists()
        assert (library_root / new).read_bytes() == b"video-bytes"
        header, row = result["relink_csv"].strip().split("\n")
        assert header == "asset_id,old_rel,new_rel,old_linux,new_linux"
        assert row.split(",")[1:3] == [old, new]
        assert row.endswith(f"/mnt/lib/{new}")
        assert (library_root / result["relink_path"]).is_file()
        assert (library_root / "Content/by-batch/Harbour/_meta/clip.mov.json").is_file()
        assert not list((library_root / Path(old).parent).glob("_meta/*.json"))

        async with server.runtime.session_factory() as session:
            reasons = {j.reason for j in (await session.execute(select(MoveJournal))).scalars()}
        assert "migration" in reasons

        again = await client.post("/api/v1/admin/migration/preview", json=body, headers=admin)
        assert again.json()["items"][0]["skipped"] == "already in the target folder"
