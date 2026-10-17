from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from uuid import uuid4

import pytest

from support import Server, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_report_filter_facet_and_detail(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        factory = server.runtime.session_factory
        used, batch = await seed_asset(factory)
        pool_only, _ = await seed_asset(factory, batch)
        other, _ = await seed_asset(factory, batch)

        body = {
            "project_name": "Harbour film",
            "machine": "edit-pc",
            "items": [
                {"asset_id": str(used.id), "uses": 3},
                {"asset_id": str(pool_only.id), "uses": 0},
                {"asset_id": str(uuid4()), "uses": 1},
            ],
        }
        first = await client.post("/api/v1/usage/report", json=body, headers=admin)
        assert first.status_code == 200
        assert first.json() == {"recorded": 1, "cleared": 0, "unknown": 1}

        listed = await client.get("/api/v1/assets", params={"used_in_project": "Harbour film"})
        assert [a["id"] for a in listed.json()["items"]] == [str(used.id)]
        any_project = await client.get("/api/v1/assets", params={"used_in_project": "*"})
        assert any_project.json()["total"] == 1
        facets = (await client.get("/api/v1/facets")).json()
        facet = next(f for f in facets["facets"] if f["key"] == "used_in_project")
        assert facet["values"] == [{"value": "Harbour film", "count": 1}]

        detail = (await client.get(f"/api/v1/assets/{used.id}")).json()
        assert [(u["project_name"], u["source"], u["machine"]) for u in detail["usage"]] == [
            ("Harbour film", "resolve", "edit-pc")
        ]

        # A later report with uses = 0 clears the Resolve row; manual rows are independent.
        body["items"] = [{"asset_id": str(used.id), "uses": 0}]
        cleared = await client.post("/api/v1/usage/report", json=body, headers=admin)
        assert cleared.json()["cleared"] == 1
        mark = await client.put(
            f"/api/v1/assets/{other.id}/usage", json={"project_name": "Pitch"}, headers=admin
        )
        assert mark.status_code == 204
        projects = (await client.get("/api/v1/usage/projects")).json()
        assert projects == [{"project_name": "Pitch", "assets": 1}]
        gone = await client.delete(
            f"/api/v1/assets/{other.id}/usage", params={"project_name": "Pitch"}, headers=admin
        )
        assert gone.status_code == 204
        assert (await client.get("/api/v1/usage/projects")).json() == []


async def test_worker_usage_endpoint_requires_a_worker_key(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        await server.setup_admin()
        anonymous = await server.client.post(
            "/api/v1/worker/usage", json={"project_name": "x", "items": []}
        )
        assert anonymous.status_code == 401
