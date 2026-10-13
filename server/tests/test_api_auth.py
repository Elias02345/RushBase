from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import select

from rushbase_server.models import ApiToken, AuditLog, Session
from support import PASSWORD, Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_setup_creates_admin_initialises_library_and_logs_in(
    server_factory: ServerFactory, database_url: str, tmp_path: Path
) -> None:
    root = tmp_path / "fresh-library"
    async with server_factory(database_url, library_root=root) as server:
        client = server.client
        status = (await client.get("/api/v1/setup")).json()
        assert status["needs_setup"] is True
        assert status["library_initialised"] is False
        assert (await client.get("/health")).json()["setup_required"] is True

        response = await client.post(
            "/api/v1/setup", json={"username": "elias", "password": PASSWORD}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["user"]["role"] == "admin"
        assert (root / "Input").is_dir()
        assert (root / ".rushbase" / "library.json").is_file()
        assert server.runtime.binding.held

        me = await client.get("/api/v1/auth/me")
        assert me.json()["username"] == "elias"

        second = await client.post("/api/v1/setup", json={"username": "x", "password": PASSWORD})
        assert second.status_code == 409
        assert (await client.get("/api/v1/setup")).json()["needs_setup"] is False
        health = (await client.get("/health")).json()
        assert health["status"] == "ok"
        assert health["library_mounted"] is True
        assert health["lease_held"] is True


async def test_setup_requires_a_configured_library_and_a_strong_password(
    server_factory: ServerFactory, database_url: str, tmp_path: Path
) -> None:
    async with server_factory(database_url) as server:
        response = await server.client.post(
            "/api/v1/setup", json={"username": "a", "password": PASSWORD}
        )
        assert response.status_code == 409
    async with server_factory(database_url, library_root=tmp_path / "lib") as server:
        weak = await server.client.post(
            "/api/v1/setup", json={"username": "a", "password": "short"}
        )
        assert weak.status_code == 422


async def test_login_cookie_flags_csrf_and_logout(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        csrf = await server.setup_admin()
        client = server.client
        client.cookies.clear()
        assert (await client.get("/api/v1/auth/me")).status_code == 401

        bad = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}
        )
        assert bad.status_code == 401
        login = await client.post(
            "/api/v1/auth/login", json={"username": "ADMIN", "password": PASSWORD}
        )
        assert login.status_code == 200  # user names are case-insensitive
        cookies = login.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith("rb_session="))
        csrf_cookie = next(c for c in cookies if c.startswith("rb_csrf="))
        assert "HttpOnly" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert "HttpOnly" not in csrf_cookie
        header = {"X-CSRF-Token": login.json()["csrf_token"]}

        # unsafe method without / with a wrong CSRF header is rejected
        assert (await client.post("/api/v1/tokens", json={"name": "t"})).status_code == 403
        wrong = await client.post(
            "/api/v1/tokens", json={"name": "t"}, headers={"X-CSRF-Token": csrf["X-CSRF-Token"]}
        )
        assert wrong.status_code == 403
        ok = await client.post("/api/v1/tokens", json={"name": "t"}, headers=header)
        assert ok.status_code == 201

        assert (await client.post("/api/v1/auth/logout", headers=header)).status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_login_is_rate_limited_and_audited(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        client = server.client
        client.cookies.clear()
        statuses = [
            (
                await client.post(
                    "/api/v1/auth/login", json={"username": "admin", "password": "nope-nope-nope"}
                )
            ).status_code
            for _ in range(4)
        ]
        assert statuses == [401, 401, 401, 429]
        blocked = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
        )
        assert blocked.status_code == 429
        assert int(blocked.headers["retry-after"]) > 0
        server.clock.advance(1000)  # window over
        assert (
            await client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}
            )
        ).status_code == 200
        async with server.runtime.session_factory() as session:
            actions = [a.action for a in (await session.execute(select(AuditLog))).scalars()]
        assert actions.count("auth.login_failed") == 3
        assert "setup.complete" in actions
        assert "auth.login" in actions


async def test_expired_sessions_are_rejected(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        assert (await server.client.get("/api/v1/auth/me")).status_code == 200
        server.clock.advance(8 * 24 * 3600)
        assert (await server.client.get("/api/v1/auth/me")).status_code == 401


async def test_roles_gate_admin_endpoints_and_personal_tokens_authenticate(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        created = await client.post(
            "/api/v1/admin/users",
            json={"username": "viewer1", "password": PASSWORD, "role": "viewer"},
            headers=admin,
        )
        assert created.status_code == 201
        duplicate = await client.post(
            "/api/v1/admin/users",
            json={"username": "VIEWER1", "password": PASSWORD},
            headers=admin,
        )
        assert duplicate.status_code == 409
        client.cookies.clear()

        login = await client.post(
            "/api/v1/auth/login", json={"username": "viewer1", "password": PASSWORD}
        )
        viewer = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert (await client.get("/api/v1/status")).status_code == 200  # viewers may read
        assert (await client.get("/api/v1/admin/workers")).status_code == 403
        assert (await client.post("/api/v1/admin/queue/pause", headers=viewer)).status_code == 403

        token = (await client.post("/api/v1/tokens", json={"name": "cli"}, headers=viewer)).json()
        assert token["token"].startswith("rbp_")
        client.cookies.clear()
        bearer = {"Authorization": f"Bearer {token['token']}"}
        assert (await client.get("/api/v1/auth/me", headers=bearer)).json()["username"] == "viewer1"
        async with server.runtime.session_factory() as session:
            stored = (await session.execute(select(ApiToken))).scalars().one()
        assert token["token"] not in (stored.token_hash, stored.prefix)  # only a hash is stored
        assert len(stored.token_hash) == 64

        client.cookies.clear()
        relogin = await client.post(
            "/api/v1/auth/login", json={"username": "viewer1", "password": PASSWORD}
        )
        viewer = {"X-CSRF-Token": relogin.json()["csrf_token"]}
        revoke = await client.delete(f"/api/v1/tokens/{token['id']}", headers=viewer)
        assert revoke.status_code == 204
        client.cookies.clear()
        assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 401


async def test_password_reset_ends_sessions_and_admin_cannot_demote_self(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        me = (await client.get("/api/v1/auth/me")).json()
        assert (
            await client.patch(
                f"/api/v1/admin/users/{me['id']}", json={"role": "viewer"}, headers=admin
            )
        ).status_code == 409
        changed = await client.patch(
            f"/api/v1/admin/users/{me['id']}",
            json={"password": "another long password"},
            headers=admin,
        )
        assert changed.status_code == 200
        async with server.runtime.session_factory() as session:
            assert (await session.execute(select(Session))).scalars().all() == []
