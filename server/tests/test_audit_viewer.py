from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from support import PASSWORD, Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_audit_viewer_filters_and_pages_with_a_keyset(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        for name in ("one", "two", "three"):
            server.clock.advance(60)
            created = await client.post("/api/v1/tokens", json={"name": name}, headers=admin)
            assert created.status_code == 201

        everything = (await client.get("/api/v1/admin/audit", params={"limit": 200})).json()
        assert everything["next_cursor"] is None
        assert [e["action"] for e in everything["items"]].count("api_token.create") == 3

        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            params: dict[str, str | int] = {"limit": 2, "action": "api_token.*"}
            if cursor:
                params["cursor"] = cursor
            page = (await client.get("/api/v1/admin/audit", params=params)).json()
            seen += [e["id"] for e in page["items"]]
            pages += 1
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert pages == 2
        assert len(seen) == len(set(seen)) == 3

        mine = (
            await client.get(
                "/api/v1/admin/audit", params={"actor_kind": "user", "action": "api_token.create"}
            )
        ).json()["items"]
        assert {e["actor_name"] for e in mine} == {"admin"}
        newest = mine[0]
        assert newest["target_kind"] == "api_token"
        assert newest["ip"]

        by_target = (
            await client.get("/api/v1/admin/audit", params={"target_id": newest["target_id"]})
        ).json()["items"]
        assert [e["id"] for e in by_target] == [newest["id"]]

        window = (
            await client.get("/api/v1/admin/audit", params={"until": mine[1]["at"], "limit": 1})
        ).json()["items"]
        assert window[0]["at"] <= mine[1]["at"]
        assert window[0]["id"] != mine[0]["id"]

        actions = (await client.get("/api/v1/admin/audit/actions")).json()
        assert "api_token.create" in actions
        assert (
            await client.get("/api/v1/admin/audit", params={"cursor": "garbage"})
        ).status_code == 422


async def test_audit_viewer_is_admin_only(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        await client.post(
            "/api/v1/admin/users",
            json={"username": "editor1", "password": PASSWORD, "role": "editor"},
            headers=admin,
        )
        client.cookies.clear()
        await client.post("/api/v1/auth/login", json={"username": "editor1", "password": PASSWORD})
        assert (await client.get("/api/v1/admin/audit")).status_code == 403
