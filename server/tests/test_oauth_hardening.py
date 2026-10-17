"""OAuth abuse limits, consent trust signals, audit trail and personal-token scopes."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from rushbase_core import uuid7
from rushbase_server.models import AuditLog, OAuthClient
from rushbase_server.oauth import service
from rushbase_server.oauth.service import OAuthError
from support import Server
from test_oauth import CALLBACK, MCP, PUBLIC, _pkce, _register, _token, grant

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]
LOOPBACK = "http://127.0.0.1:8123/cb"


async def _actions(server: Server) -> list[str]:
    async with server.runtime.session_factory() as db:
        return list((await db.execute(select(AuditLog.action))).scalars())


async def test_registrations_are_capped_per_address_and_hour(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        body = {"client_name": "x", "redirect_uris": [CALLBACK]}
        statuses = []
        for _ in range(4):
            for _ in range(10):
                statuses.append(
                    (await server.client.post("/oauth/register", json=body)).status_code
                )
            server.clock.advance(61)  # per-minute bucket refilled, hourly cap still counts
        assert statuses[:30] == [201] * 30
        assert set(statuses[30:]) == {429}


async def test_authorize_and_token_endpoints_are_rate_limited_per_address(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        codes = [(await server.client.get("/oauth/authorize")).status_code for _ in range(70)]
        assert codes[-1] == 429
        assert codes[0] != 429
        server.clock.advance(120)
        wrong = {"grant_type": "authorization_code", "client_id": "rbclient_nope"}
        results = [(await _token(server, **wrong)).status_code for _ in range(130)]
        assert 429 in results[100:]


async def test_failed_metadata_fetches_are_cached_negatively(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    calls: list[str] = []

    async def failing(host: str) -> list[str]:
        calls.append(host)
        return ["10.0.0.1"]

    url = "https://client.example.org/meta.json"
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        now = server.clock.now()
        async with server.runtime.session_factory() as db:
            for _ in range(3):
                with pytest.raises(OAuthError):
                    await service.load_client(db, url, now, resolver=failing)
        assert calls == ["client.example.org"]
        later = now + service.NEGATIVE_CACHE_TTL + timedelta(seconds=1)
        async with server.runtime.session_factory() as db:
            with pytest.raises(OAuthError):
                await service.load_client(db, url, later, resolver=failing)
        assert len(calls) == 2


async def test_unused_metadata_clients_are_purged_and_capped(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        now = datetime.now(UTC)  # created_at comes from the database clock
        async with server.runtime.session_factory() as db:
            for index in range(service.MAX_CIMD_CLIENTS):
                db.add(
                    OAuthClient(
                        id=uuid7(),
                        client_id=f"https://c{index}.example.org/m.json",
                        kind="cimd",
                        name="c",
                        redirect_uris=[CALLBACK],
                    )
                )
            await db.commit()
            with pytest.raises(OAuthError) as full:
                await service._make_room_for_cimd_client(db, now)
            assert full.value.status == 429
            await service._make_room_for_cimd_client(db, now + service.UNUSED_CLIENT_TTL * 2)
            left = (
                await db.execute(select(OAuthClient.id).where(OAuthClient.kind == "cimd"))
            ).all()
            assert left == []


async def test_consent_page_marks_unverified_clients(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        _, challenge = _pkce()

        def params(client_id: str, redirect: str) -> dict[str, str]:
            return {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect,
                "scope": "read",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": MCP,
            }

        loopback = await server.client.post(
            "/oauth/register", json={"client_name": "Totally Claude", "redirect_uris": [LOOPBACK]}
        )
        page = await server.client.get(
            "/oauth/authorize", params=params(loopback.json()["client_id"], LOOPBACK)
        )
        assert "Nicht verifizierter Client" in page.text
        assert "127.0.0.1:8123" in page.text
        known = await _register(server)
        page = await server.client.get("/oauth/authorize", params=params(known, CALLBACK))
        assert "Nicht verifizierter Client" not in page.text
        assert "Bekannter Anbieter" in page.text


async def test_oauth_token_lifecycle_and_gateway_rejections_are_audited(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        await server.setup_admin()
        client_id, tokens = await grant(server)
        refreshed = await _token(
            server,
            grant_type="refresh_token",
            client_id=client_id,
            refresh_token=str(tokens["refresh_token"]),
            resource=MCP,
        )
        assert refreshed.status_code == 200
        await server.client.post("/oauth/revoke", data={"token": refreshed.json()["access_token"]})
        for _ in range(20):
            rejected = await server.client.post("/mcp", headers={"Authorization": "Bearer nope"})
            assert rejected.status_code == 401
        actions = await _actions(server)
        assert "oauth.token_issued" in actions
        assert "oauth.token_refreshed" in actions
        assert "oauth.token_revoked" in actions
        assert 1 <= actions.count("mcp.unauthorized") <= 6  # rate-limited audit


async def test_read_only_personal_token_cannot_change_anything(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        read_only = (
            await server.client.post(
                "/api/v1/tokens", json={"name": "ro", "scopes": ["read"]}, headers=admin
            )
        ).json()["token"]
        full = (
            await server.client.post("/api/v1/tokens", json={"name": "rw"}, headers=admin)
        ).json()["token"]
        bad = await server.client.post(
            "/api/v1/tokens", json={"name": "x", "scopes": ["root"]}, headers=admin
        )
        assert bad.status_code == 422
        server.client.cookies.clear()
        ro = {"Authorization": f"Bearer {read_only}"}
        assert (await server.client.get("/api/v1/auth/me", headers=ro)).status_code == 200
        denied = await server.client.post("/api/v1/tokens", json={"name": "y"}, headers=ro)
        assert denied.status_code == 403
        rw = {"Authorization": f"Bearer {full}"}
        assert (
            await server.client.post("/api/v1/tokens", json={"name": "z"}, headers=rw)
        ).status_code == 201
