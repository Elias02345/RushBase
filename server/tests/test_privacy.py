from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import select

from rushbase_server.models import Asset, AssetTag, AuditLog, TaxField, TaxValue
from support import Server, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def test_lock_by_batch_and_person_sets_consent(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        factory = server.runtime.session_factory
        first, batch = await seed_asset(factory)
        second, _ = await seed_asset(factory, batch)
        tagged, _ = await seed_asset(factory)
        named, _ = await seed_asset(factory)
        other, _ = await seed_asset(factory)
        async with factory() as session:
            field = TaxField(key="person", label_de="Person", label_en="Person", kind="multi")
            session.add(field)
            await session.flush()
            value = TaxValue(
                field_id=field.id,
                value="anna-berg",
                label_de="Anna Berg",
                synonyms=["Anni"],
                status="approved",
            )
            session.add(value)
            await session.flush()
            session.add(
                AssetTag(
                    asset_id=tagged.id,
                    field_id=field.id,
                    value_id=value.id,
                    source="human",
                    confidence=1.0,
                )
            )
            (await session.get_one(Asset, named.id)).title = "Interview mit anna berg"
            await session.commit()
        client = server.client

        both = {"batch_id": str(batch.id)}
        dry = await client.post("/api/v1/privacy/lock", json=both, headers=admin)
        assert dry.json()["matched"] == 2
        assert dry.json()["updated"] == 0
        done = await client.post(
            "/api/v1/privacy/lock",
            json={**both, "dry_run": False, "consent": "no", "consent_ref": "Vertrag 7"},
            headers=admin,
        )
        assert done.json()["updated"] == 2

        person = {"person": "Anna Berg", "dry_run": False, "consent": "yes"}
        found = await client.post("/api/v1/privacy/lock", json=person, headers=admin)
        assert found.json()["matched"] == 2  # the tagged and the named asset
        by_synonym = await client.post(
            "/api/v1/privacy/lock", json={"person": "anni"}, headers=admin
        )
        assert by_synonym.json()["matched"] == 1

        async with factory() as session:
            rows = {a.id: a for a in (await session.execute(select(Asset))).scalars()}
            actions = set((await session.execute(select(AuditLog.action))).scalars())
        assert rows[first.id].locked
        assert rows[second.id].consent == "no"
        assert rows[second.id].consent_ref == "Vertrag 7"
        assert rows[tagged.id].consent == "yes"
        assert rows[named.id].locked
        assert not rows[other.id].locked
        assert "privacy.lock" in actions

        invalid = await client.post("/api/v1/privacy/lock", json={}, headers=admin)
        assert invalid.status_code == 422


async def test_bulk_edit_sets_consent_and_person_flag(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        asset, _ = await seed_asset(server.runtime.session_factory)
        response = await server.client.post(
            "/api/v1/assets/bulk",
            json={
                "asset_ids": [str(asset.id)],
                "consent": "yes",
                "consent_ref": "Vertrag 1",
                "person_identifiable": "yes",
            },
            headers=admin,
        )
        assert response.status_code == 200
        detail = (await server.client.get(f"/api/v1/assets/{asset.id}")).json()
        assert detail["ai"]["consent"] == "yes"
        assert detail["ai"]["person_identifiable"] == "yes"
        assert detail["ai"]["consent_ref"] == "Vertrag 1"
