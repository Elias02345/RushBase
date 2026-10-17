"""Security hardening: trusted proxies, secure cookies, setup token, metrics token."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from rushbase_server.main import uvicorn_options
from support import PASSWORD, Server, make_settings

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]
DB = "postgresql+psycopg://u:p@h/db"


def test_forwarded_headers_are_trusted_only_from_configured_proxies() -> None:
    default = uvicorn_options(make_settings(DB))
    assert default["forwarded_allow_ips"] == "127.0.0.1"
    custom = uvicorn_options(make_settings(DB, trusted_proxies="172.18.0.0/16"))
    assert custom["forwarded_allow_ips"] == "172.18.0.0/16"
    assert custom["proxy_headers"] is True


@pytest.mark.parametrize(
    ("public_url", "explicit", "expected"),
    [
        (None, None, False),
        ("https://media.example.org", None, True),
        ("http://localhost:8080", None, False),
        ("https://media.example.org", False, False),
        (None, True, True),
    ],
)
def test_secure_cookie_default_follows_public_url(
    public_url: str | None, explicit: bool | None, expected: bool
) -> None:
    settings = make_settings(DB, public_url=public_url, cookie_secure=explicit)
    assert settings.use_secure_cookies is expected


def test_metrics_need_a_token() -> None:
    with pytest.raises(ValidationError, match="metrics_token"):
        make_settings(DB, metrics_enabled=True)


async def test_setup_requires_the_setup_token(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    code = "s3tup-c0de-" + "x" * 8
    async with server_factory(
        database_url, library_root=library_root, setup_token=SecretStr(code)
    ) as server:
        status = (await server.client.get("/api/v1/setup")).json()
        assert status["setup_token_required"] is True
        body = {"username": "admin", "password": PASSWORD}
        wrong = await server.client.post("/api/v1/setup", json={**body, "setup_token": "nope"})
        assert wrong.status_code == 403
        restore = await server.client.post("/api/v1/setup/restore", json={"backup_name": "x"})
        assert restore.status_code == 403
        ok = await server.client.post("/api/v1/setup", json={**body, "setup_token": code})
        assert ok.status_code == 201


async def test_setup_token_attempts_are_rate_limited(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(
        database_url, library_root=library_root, setup_token=SecretStr("c" * 20)
    ) as server:
        body = {"username": "admin", "password": PASSWORD, "setup_token": "bad"}
        codes = [
            (await server.client.post("/api/v1/setup", json=body)).status_code for _ in range(5)
        ]
        assert codes[-1] == 429


async def test_oversized_request_bodies_are_rejected(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(
        database_url, library_root=library_root, max_body_bytes=2048
    ) as server:
        declared = await server.client.post("/api/v1/auth/login", content=b"x" * 5000)
        assert declared.status_code == 413
        assert "default-src 'self'" in declared.headers["content-security-policy"]

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(10):
                yield b"y" * 500  # no Content-Length: counted while streaming

        streamed = await server.client.post("/api/v1/auth/login", content=chunks())
        assert streamed.status_code == 413
        small = await server.client.post(
            "/api/v1/auth/login", json={"username": "a", "password": "b"}
        )
        assert small.status_code == 401


async def test_setup_without_token_setting_stays_open(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        assert (await server.client.get("/api/v1/setup")).json()["setup_token_required"] is False
        await server.setup_admin()
