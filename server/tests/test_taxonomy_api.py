from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_support import (
    classify_output,
    description,
    fake_job,
    seed_shots_asset,
    tag,
    vision_output,
)
from pipeline import enroll
from rushbase_server.models import Job, StageResult
from rushbase_server.services import ai_results
from support import PASSWORD, Server

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


async def make_editor(server: Server, admin: dict[str, str]) -> dict[str, str]:
    created = await server.client.post(
        "/api/v1/admin/users",
        json={"username": "ed", "password": PASSWORD, "role": "editor"},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    await server.client.post("/api/v1/auth/logout", headers=admin)
    login = await server.client.post(
        "/api/v1/auth/login", json={"username": "ed", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return {"X-CSRF-Token": login.json()["csrf_token"]}


async def test_proposal_to_approval_flow(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        worker = await enroll(server, admin)
        field = (
            await server.client.post(
                "/api/v1/taxonomy/fields",
                json={"key": "location", "label_de": "Ort", "label_en": "Location"},
                headers=admin,
            )
        ).json()
        harbour = await server.client.post(
            f"/api/v1/taxonomy/fields/{field['id']}/values",
            json={"value": "harbour", "label_de": "Hafen", "synonyms": ["port"]},
            headers=admin,
        )
        assert harbour.json()["status"] == "approved"
        snapshot = (await server.client.get("/api/v1/worker/taxonomy", headers=worker)).json()
        assert snapshot["version"] == 2
        assert snapshot["fields"][0]["values"] == [
            {"value": "harbour", "synonyms": ["port"], "description": ""}
        ]
        # an AI classification proposes "pier" for an asset that was classified with version 1
        async with server.runtime.session_factory() as session:
            asset = await seed_shots_asset(session, shots=1)
            asset_id = asset.id
            job = fake_job(asset.id, "classify")
            output = classify_output(
                [tag("harbour", 0)],
                proposals=[{"field": "location", "term": "pier", "confidence": 0.7}],
            )
            await ai_results.apply_output(session, job, output)
            await ai_results.apply_output(
                session, fake_job(asset.id, "vision"), vision_output([description(0, "x")])
            )
            session.add(
                StageResult(
                    asset_id=asset.id,
                    job_id=None,
                    stage="classify",
                    ok=True,
                    output=output.model_dump(mode="json", by_alias=True),
                    finished_at=server.clock.now(),
                )
            )
            await session.commit()
        editor = await make_editor(server, admin)
        proposals = (await server.client.get("/api/v1/taxonomy/proposals")).json()
        assert [(p["value"], p["proposal_count"]) for p in proposals] == [("pier", 1)]
        assert proposals[0]["examples"][0]["asset_id"] == str(asset_id)
        approved = await server.client.post(
            f"/api/v1/taxonomy/values/{proposals[0]['id']}/approve", headers=editor
        )
        assert approved.json()["status"] == "approved"
        async with server.runtime.session_factory() as session:
            queued = (
                (await session.execute(select(Job).where(Job.stage == "classify"))).scalars().all()
            )
            assert [j.asset_id for j in queued] == [asset_id]
        taxonomy = (await server.client.get("/api/v1/taxonomy")).json()
        assert taxonomy["version"] == 3
        # an editor may propose but not create approved values or delete
        proposed = await server.client.post(
            f"/api/v1/taxonomy/fields/{field['id']}/values",
            json={"value": "dock"},
            headers=editor,
        )
        assert proposed.json()["status"] == "proposed"
        denied = await server.client.post(
            f"/api/v1/taxonomy/values/{proposals[0]['id']}/deprecate", headers=editor
        )
        assert denied.status_code == 403


async def test_merge_moves_tags_and_keeps_names_as_synonyms(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        field = (
            await server.client.post(
                "/api/v1/taxonomy/fields",
                json={"key": "location", "label_de": "Ort", "label_en": "Location"},
                headers=admin,
            )
        ).json()
        ids = {}
        for name in ("harbour", "port"):
            response = await server.client.post(
                f"/api/v1/taxonomy/fields/{field['id']}/values", json={"value": name}, headers=admin
            )
            ids[name] = response.json()["id"]
        async with server.runtime.session_factory() as session:
            asset = await seed_shots_asset(session, shots=1)
            await ai_results.apply_output(
                session, fake_job(asset.id, "classify"), classify_output([tag("port")])
            )
            await session.commit()
        merged = await server.client.post(
            f"/api/v1/taxonomy/values/{ids['port']}/merge",
            json={"target_id": ids["harbour"]},
            headers=admin,
        )
        assert merged.json()["synonyms"] == ["port"]
        facets = (await server.client.get("/api/v1/facets")).json()["facets"]
        tag_facet = next(f for f in facets if f["key"] == "tag.location")
        assert tag_facet["values"] == [{"value": "location:harbour", "count": 1}]
        found = await server.client.get("/api/v1/assets", params={"tag": "location:harbour"})
        assert found.json()["total"] == 1
        none = await server.client.get("/api/v1/assets", params={"tag": "location:other"})
        assert none.json()["total"] == 0
        bad = await server.client.post(
            f"/api/v1/taxonomy/values/{ids['harbour']}/merge",
            json={"target_id": ids["harbour"]},
            headers=admin,
        )
        assert bad.status_code == 409
