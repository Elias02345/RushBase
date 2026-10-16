from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest

from rushbase_server import get_version

ClientFactory = Callable[[str], AbstractAsyncContextManager[httpx.AsyncClient]]


async def test_health_degraded_without_database(
    client_factory: ClientFactory, unreachable_url: str
) -> None:
    async with client_factory(unreachable_url) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["version"] == get_version()
    assert body["database"] is False


@pytest.mark.postgres
async def test_health_ok_with_database(client_factory: ClientFactory, database_url: str) -> None:
    async with client_factory(database_url) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": get_version(),
        "database": True,
        "library_mounted": None,
        "lease_held": None,
        "setup_required": True,
        "warnings": ["mcp_public_url_missing"],
    }


async def test_openapi_documents_health(
    client_factory: ClientFactory, unreachable_url: str
) -> None:
    async with client_factory(unreachable_url) as client:
        schema = (await client.get("/openapi.json")).json()
    assert "/health" in schema["paths"]
    assert "/api/v1/worker/claim" in schema["paths"]
