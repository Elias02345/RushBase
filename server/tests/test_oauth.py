import json
import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from rushbase_server.oauth import cimd, redirects, service
from support import PASSWORD, Server

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]
PUBLIC = "https://media.example.org"
MCP = f"{PUBLIC}/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, service.pkce_challenge(verifier)


async def _register(server: Server, redirect: str = CALLBACK) -> str:
    response = await server.client.post(
        "/oauth/register", json={"client_name": "Claude", "redirect_uris": [redirect]}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["client_id"])


async def authorize(
    server: Server,
    client_id: str,
    challenge: str,
    *,
    redirect: str = CALLBACK,
    scope: str = "read needs",
    decision: str = "allow",
    resource: str = MCP,
) -> httpx.Response:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": scope,
        "state": "xyz",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    page = await server.client.get("/oauth/authorize", params=params)
    assert page.status_code == 200, page.text
    assert "Claude" in page.text
    assert "claude.ai" in page.text
    csrf = server.client.cookies["rb_oauth_csrf"]
    return await server.client.post(
        "/oauth/authorize",
        data={**params, "csrf": csrf, "decision": decision},
    )


def _code(response: httpx.Response) -> str:
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["xyz"]
    assert query["iss"] == [PUBLIC]
    return query["code"][0]


async def _token(server: Server, **data: str) -> httpx.Response:
    return await server.client.post("/oauth/token", data=data)


async def _grant(server: Server) -> tuple[str, dict[str, object]]:
    client_id = await _register(server)
    verifier, challenge = _pkce()
    code = _code(await authorize(server, client_id, challenge))
    response = await _token(
        server,
        grant_type="authorization_code",
        client_id=client_id,
        code=code,
        redirect_uri=CALLBACK,
        code_verifier=verifier,
        resource=MCP,
    )
    assert response.status_code == 200, response.text
    return client_id, response.json()


@pytest.mark.postgres
async def test_metadata_and_unconfigured(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        meta = (await server.client.get("/.well-known/oauth-authorization-server")).json()
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert meta["client_id_metadata_document_supported"] is True
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            prm = (await server.client.get(path)).json()
            assert prm["resource"] == MCP
            assert prm["authorization_servers"] == [PUBLIC]
        assert (await server.client.get("/health")).json()["warnings"] == []
    async with server_factory(database_url, library_root=library_root) as server:
        assert (
            await server.client.get("/.well-known/oauth-authorization-server")
        ).status_code == 503
        assert (await server.client.get("/health")).json()["warnings"] == ["mcp_public_url_missing"]


@pytest.mark.postgres
async def test_pkce_flow_refresh_rotation_and_reuse(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        await server.setup_admin()
        client_id, tokens = await _grant(server)
        assert tokens["token_type"] == "Bearer"
        assert tokens["expires_in"] == 3600
        assert tokens["scope"] == "read needs"
        async with server.runtime.session_factory() as db:
            verified = await service.verify_access(
                db, str(tokens["access_token"]), MCP, server.runtime.clock.now()
            )
            assert verified is not None
            assert verified.scopes == ("read", "needs")
            assert (
                await service.verify_access(
                    db, str(tokens["access_token"]), "https://other.example/mcp", server.clock.now()
                )
                is None
            )  # audience mismatch
            assert (
                await service.verify_access(
                    db, str(tokens["access_token"]), MCP, server.clock.now() + timedelta(hours=2)
                )
                is None
            )  # expired
        first = tokens["refresh_token"]
        rotated = await _token(
            server, grant_type="refresh_token", client_id=client_id, refresh_token=str(first)
        )
        assert rotated.status_code == 200
        second = rotated.json()["refresh_token"]
        assert second != first
        # replaying the old refresh token ends the whole grant
        replay = await _token(
            server, grant_type="refresh_token", client_id=client_id, refresh_token=str(first)
        )
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"
        after = await _token(
            server, grant_type="refresh_token", client_id=client_id, refresh_token=str(second)
        )
        assert after.json()["error"] == "invalid_grant"
        async with server.runtime.session_factory() as db:
            assert (
                await service.verify_access(
                    db, rotated.json()["access_token"], MCP, server.clock.now()
                )
                is None
            )
        bad = await _token(
            server, grant_type="refresh_token", client_id=client_id, refresh_token="nope"
        )
        assert bad.json()["error"] == "invalid_grant"


@pytest.mark.postgres
async def test_pkce_failures_and_code_reuse(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        await server.setup_admin()
        client_id = await _register(server)
        verifier, challenge = _pkce()
        code = _code(await authorize(server, client_id, challenge))
        wrong = await _token(
            server, grant_type="authorization_code", client_id=client_id, code=code,
            redirect_uri=CALLBACK, code_verifier=secrets.token_urlsafe(48),
        )  # fmt: skip
        assert wrong.json()["error"] == "invalid_grant"
        # a code is burnt by a failed attempt
        again = await _token(
            server, grant_type="authorization_code", client_id=client_id, code=code,
            redirect_uri=CALLBACK, code_verifier=verifier,
        )  # fmt: skip
        assert again.json()["error"] == "invalid_grant"
        # plain PKCE and a missing challenge are refused
        page = await server.client.get(
            "/oauth/authorize",
            params={
                "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                "code_challenge": challenge, "code_challenge_method": "plain",
            },
        )  # fmt: skip
        assert page.status_code == 303
        assert "error=invalid_request" in page.headers["location"]
        # a wrong resource is refused
        bad = await server.client.get(
            "/oauth/authorize",
            params={
                "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "resource": "https://evil.example/mcp",
            },
        )  # fmt: skip
        assert "error=invalid_target" in bad.headers["location"]


@pytest.mark.postgres
async def test_redirect_and_registration_rules(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        await server.setup_admin()
        for bad in ("https://evil.example/cb", "http://claude.ai/api/mcp/auth_callback"):
            r = await server.client.post(
                "/oauth/register", json={"client_name": "x", "redirect_uris": [bad]}
            )
            assert r.status_code == 400
            assert r.json()["error"] == "invalid_redirect_uri"
        loop = await _register(server, "http://127.0.0.1:5555/callback")
        _, challenge = _pkce()
        ok = await server.client.get(
            "/oauth/authorize",
            params={
                "response_type": "code", "client_id": loop, "code_challenge": challenge,
                "redirect_uri": "http://127.0.0.1:9999/callback", "code_challenge_method": "S256",
            },
        )  # fmt: skip
        assert ok.status_code == 200  # loopback ignores the port
        client_id = await _register(server)
        mismatch = await server.client.get(
            "/oauth/authorize",
            params={
                "response_type": "code", "client_id": client_id, "code_challenge": challenge,
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback/x",
                "code_challenge_method": "S256",
            },
        )  # fmt: skip
        assert mismatch.status_code == 400  # error page, never a redirect
        assert "location" not in mismatch.headers


@pytest.mark.postgres
async def test_consent_needs_login_and_editor_role(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, public_url=PUBLIC, library_root=library_root) as server:
        csrf = await server.setup_admin()
        made = await server.client.post(
            "/api/v1/admin/users",
            headers=csrf,
            json={"username": "vera", "password": PASSWORD, "role": "viewer"},
        )
        assert made.status_code == 201, made.text
        server.client.cookies.clear()
        client_id = await _register(server)
        _, challenge = _pkce()
        params = {
            "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }  # fmt: skip
        await server.client.get("/oauth/authorize", params=params)
        token = server.client.cookies["rb_oauth_csrf"]
        form = {**params, "csrf": token, "decision": "allow"}

        def fresh() -> dict[str, str]:  # an error page rotates the CSRF token
            return {**form, "csrf": server.client.cookies["rb_oauth_csrf"]}

        no_csrf = await server.client.post("/oauth/authorize", data={**form, "csrf": "x"})
        assert no_csrf.status_code == 403
        wrong = await server.client.post(
            "/oauth/authorize", data={**fresh(), "username": "vera", "password": "wrong password!"}
        )
        assert wrong.status_code == 401
        viewer = await server.client.post(
            "/oauth/authorize", data={**fresh(), "username": "vera", "password": PASSWORD}
        )
        assert viewer.status_code == 403
        deny = await server.client.post("/oauth/authorize", data={**fresh(), "decision": "deny"})
        assert "error=access_denied" in deny.headers["location"]
        admin = await server.client.post(
            "/oauth/authorize", data={**fresh(), "username": "admin", "password": PASSWORD}
        )
        assert admin.status_code == 303
        assert "code=" in admin.headers["location"]


# --- redirects and CIMD (no database) -----------------------------------------------------


def test_redirect_policy() -> None:
    assert redirects.is_allowed(CALLBACK)
    assert redirects.is_allowed("http://localhost:1234/cb")
    assert not redirects.is_allowed("http://evil.localhost.example/cb")
    assert not redirects.is_allowed("https://claude.ai/api/mcp/auth_callback/")
    assert redirects.is_allowed("https://app.example/cb", ["https://app.example/cb"])
    assert redirects.matches("http://127.0.0.1:99/cb", ["http://127.0.0.1:1/cb"])
    assert not redirects.matches("http://127.0.0.1:99/other", ["http://127.0.0.1:1/cb"])
    assert not redirects.matches("https://claude.ai/x", [CALLBACK])


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1",
        "0.0.0.0",  # noqa: S104 "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1",
        "::ffff:10.1.2.3", "64:ff9b::7f00:1", "2002:7f00:1::1", "2001:db8::1", "garbage",
    ],
)  # fmt: skip
def test_blocked_addresses(address: str) -> None:
    assert not cimd.address_allowed(address)


def test_public_addresses_allowed() -> None:
    assert cimd.address_allowed("93.184.216.34")
    assert cimd.address_allowed("2606:4700:4700::1111")


URL = "https://client.example.org/meta.json"


def _doc(**extra: object) -> bytes:
    return json.dumps(
        {"client_id": URL, "client_name": "Doc", "redirect_uris": [CALLBACK], **extra}
    ).encode()


async def _public(_: str) -> list[str]:
    return ["93.184.216.34"]


async def test_cimd_fetch_success_and_connects_to_resolved_ip() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_doc(), headers={"cache-control": "max-age=900"})

    meta = await cimd.fetch_client_metadata(
        URL, resolver=_public, transport=httpx.MockTransport(handler)
    )
    assert meta.name == "Doc"
    assert meta.cache_seconds == 900
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "client.example.org"


@pytest.mark.parametrize("resolved", [["10.0.0.1"], ["93.184.216.34", "127.0.0.1"], ["::1"], []])
async def test_cimd_blocks_private_resolution(resolved: list[str]) -> None:
    async def resolver(_: str) -> list[str]:
        return resolved

    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=_doc())

    with pytest.raises(cimd.CimdError):
        await cimd.fetch_client_metadata(
            URL, resolver=resolver, transport=httpx.MockTransport(handler)
        )
    assert not called


@pytest.mark.parametrize(
    "url",
    [
        "http://client.example.org/m.json", "https://127.0.0.1/m.json", "https://[::1]/m.json",
        "https://user:pw@client.example.org/m.json", "https://client.example.org:8443/m.json",
        "https://client.example.org/", "https://client.example.org/a#frag",
    ],
)  # fmt: skip
async def test_cimd_rejects_bad_urls(url: str) -> None:
    with pytest.raises(cimd.CimdError):
        await cimd.fetch_client_metadata(
            url, resolver=_public, transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )


async def test_cimd_never_follows_redirects() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest"})

    with pytest.raises(cimd.CimdError):
        await cimd.fetch_client_metadata(
            URL, resolver=_public, transport=httpx.MockTransport(handler)
        )
    assert len(hits) == 1


async def test_cimd_size_cap_and_validation() -> None:
    big = httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"x" * (cimd.MAX_BODY_BYTES + 1))
    )
    with pytest.raises(cimd.CimdError, match="too large"):
        await cimd.fetch_client_metadata(URL, resolver=_public, transport=big)
    wrong_id = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            content=json.dumps({"client_id": "https://x.example/y", "redirect_uris": [CALLBACK]}),
        )
    )
    with pytest.raises(cimd.CimdError):
        await cimd.fetch_client_metadata(URL, resolver=_public, transport=wrong_id)
    secret = httpx.MockTransport(lambda r: httpx.Response(200, content=_doc(client_secret="s")))
    with pytest.raises(cimd.CimdError):
        await cimd.fetch_client_metadata(URL, resolver=_public, transport=secret)


async def test_cimd_etag_revalidation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304)

    meta = await cimd.fetch_client_metadata(
        URL, resolver=_public, transport=httpx.MockTransport(handler), etag='"v1"'
    )
    assert meta.not_modified
