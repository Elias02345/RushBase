from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select

from rushbase_server.models import Job, StageResult
from support import Server, seed_asset

pytestmark = pytest.mark.postgres

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]


def _result(
    asset_id: UUID, day: int, output: dict[str, Any], *, version: str | None = None
) -> StageResult:
    return StageResult(
        asset_id=asset_id,
        stage="classify",
        ok=True,
        output=output,
        model_id="rushbase-worker/classify",
        model_version=version,
        finished_at=datetime(2026, 10, day, tzinfo=UTC),
    )


def _model(revision: str, profile: str) -> dict[str, Any]:
    return {"model": {"model_id": "qwen", "revision": revision, "profile": profile}}


async def test_reprocess_by_model_version_with_dry_run(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        client = server.client
        factory = server.runtime.session_factory
        old, batch = await seed_asset(factory, stages=("probe",))
        new, _ = await seed_asset(factory, batch, stages=("probe",))
        bare, _ = await seed_asset(factory, batch, stages=("probe",))
        never, _ = await seed_asset(factory, batch, stages=("probe",))
        async with factory() as session:
            session.add_all(
                [
                    _result(old.id, 1, _model("0.1", "cpu")),  # superseded by the next one
                    _result(old.id, 2, _model("1.2", "gpu8")),
                    _result(new.id, 2, _model("1.10", "cpu")),
                    _result(bare.id, 2, {}, version="0.9"),
                ]
            )
            await session.commit()

        usage = (await client.get("/api/v1/admin/reprocess/models")).json()
        counts = {(u["model_id"], u["model_version"], u["profile"]): u["assets"] for u in usage}
        assert counts == {
            ("qwen", "1.2", "gpu8"): 1,
            ("qwen", "1.10", "cpu"): 1,
            ("rushbase-worker/classify", "0.9", None): 1,
        }

        body: dict[str, Any] = {
            "stage": "classify",
            "field": "model_version",
            "op": "lt",
            "value": "1.10",
        }
        dry = (
            await client.post("/api/v1/admin/reprocess/by-model", json=body, headers=admin)
        ).json()
        assert (dry["dry_run"], dry["matched"], dry["queued"]) == (True, 2, 0)
        assert sorted(v["value"] for v in dry["by_value"]) == ["0.9", "1.2"]
        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Job).where(Job.stage == "classify")
                )
                == 0
            )

        real = (
            await client.post(
                "/api/v1/admin/reprocess/by-model", json={**body, "dry_run": False}, headers=admin
            )
        ).json()
        assert (real["matched"], real["queued"], real["skipped"]) == (2, 2, 0)
        async with factory() as session:
            queued = set(
                (
                    await session.execute(select(Job.asset_id).where(Job.stage == "classify"))
                ).scalars()
            )
        assert queued == {old.id, bare.id}
        assert never.id not in queued  # no result to compare

        again = (
            await client.post(
                "/api/v1/admin/reprocess/by-model", json={**body, "dry_run": False}, headers=admin
            )
        ).json()
        assert (again["matched"], again["queued"], again["skipped"]) == (2, 0, 2)  # jobs are open

        different = (
            await client.post(
                "/api/v1/admin/reprocess/by-model",
                json={"stage": "classify", "field": "profile", "op": "ne", "value": "cpu"},
                headers=admin,
            )
        ).json()
        assert different["matched"] == 2  # gpu8 and the result without a profile
