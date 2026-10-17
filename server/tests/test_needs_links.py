from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from support import Server, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_asset_lists_the_needs_items_it_is_a_candidate_for(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        asset, batch = await seed_asset(server.runtime.session_factory)
        other, _ = await seed_asset(server.runtime.session_factory, batch)
        created = await client.post(
            "/api/v1/needs",
            json={
                "title": "Harbour film",
                "items": [{"description": "Boats at dawn"}, {"description": "Cranes"}],
            },
            headers=admin,
        )
        assert created.status_code == 201
        items = created.json()["items"]
        assert (await client.get(f"/api/v1/needs/for-asset/{asset.id}")).json() == []

        for item, decision in ((items[0], "candidate"), (items[1], "confirmed")):
            put = await client.put(
                f"/api/v1/needs/items/{item['id']}/candidates/{asset.id}",
                json={"status": decision},
                headers=admin,
            )
            assert put.status_code == 200
        await client.put(
            f"/api/v1/needs/items/{items[0]['id']}/candidates/{other.id}",
            json={"status": "candidate"},
            headers=admin,
        )

        links = (await client.get(f"/api/v1/needs/for-asset/{asset.id}")).json()
        assert {(link["item_description"], link["status"]) for link in links} == {
            ("Boats at dawn", "candidate"),
            ("Cranes", "confirmed"),
        }
        assert all(link["list_title"] == "Harbour film" for link in links)
        cranes = next(link for link in links if link["item_description"] == "Cranes")
        assert cranes["item_status"] == "fulfilled"

        rejected = await client.put(
            f"/api/v1/needs/items/{items[0]['id']}/candidates/{asset.id}",
            json={"status": "rejected"},
            headers=admin,
        )
        assert rejected.status_code == 200
        again = (await client.get(f"/api/v1/needs/for-asset/{asset.id}")).json()
        assert {link["status"] for link in again} == {"rejected", "confirmed"}
