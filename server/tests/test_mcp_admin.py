from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from rushbase_server.oauth import service
from support import Server
from test_oauth import MCP, PUBLIC, grant

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_admin_overview_revoke_and_audit(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        headers = await server.setup_admin()
        client_id, tokens = await grant(server)
        overview = (await server.client.get("/api/v1/admin/mcp")).json()
        assert overview["enabled"] is True
        assert overview["mcp_url"] == MCP
        (entry,) = overview["clients"]
        assert entry["client_id"] == client_id
        assert entry["scopes"] == ["needs", "read"]
        assert entry["grants"] == 1

        saved = await server.client.put(
            "/api/v1/admin/mcp/redirect-uris",
            headers=headers,
            json={"uris": ["https://app.example.org/cb"]},
        )
        assert saved.json() == ["https://app.example.org/cb"]
        bad = await server.client.put(
            "/api/v1/admin/mcp/redirect-uris", headers=headers, json={"uris": ["http://x/cb"]}
        )
        assert bad.status_code == 422

        revoked = await server.client.post(
            f"/api/v1/admin/mcp/clients/{entry['id']}/revoke", headers=headers
        )
        assert revoked.status_code == 204
        async with server.runtime.session_factory() as db:
            assert (
                await service.verify_access(
                    db, str(tokens["access_token"]), MCP, server.clock.now()
                )
                is None
            )
        refused = await server.client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": str(tokens["refresh_token"]),
            },
        )
        assert refused.status_code == 401
        audit = (await server.client.get("/api/v1/admin/mcp/audit")).json()
        assert audit["items"] == []


async def test_needs_rest_api(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        headers = await server.setup_admin()
        created = await server.client.post(
            "/api/v1/needs",
            headers=headers,
            json={
                "title": "Trailer",
                "items": [{"description": "Hafen", "filters": {"media_type": "video"}}],
            },
        )
        assert created.status_code == 201
        need = created.json()
        item = need["items"][0]
        assert item["filters"] == {"media_type": "video"}
        patched = await server.client.patch(
            f"/api/v1/needs/{need['id']}", headers=headers, json={"status": "closed"}
        )
        assert patched.json()["status"] == "closed"
        edited = await server.client.patch(
            f"/api/v1/needs/items/{item['id']}", headers=headers, json={"count_wanted": 3}
        )
        assert edited.status_code == 200
        missing = await server.client.get("/api/v1/needs/00000000-0000-0000-0000-000000000000")
        assert missing.status_code == 404
        unknown = await server.client.put(
            f"/api/v1/needs/items/{item['id']}/candidates/00000000-0000-0000-0000-000000000000",
            headers=headers,
            json={"status": "confirmed"},
        )
        assert unknown.status_code == 404
