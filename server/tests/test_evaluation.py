"""Eval regression on fixtures (no models) and the golden API."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_support import (
    classify_output,
    description,
    estimate,
    fake_job,
    seed_shots_asset,
    seed_taxonomy,
    tag,
    vision_output,
)
from rushbase_server.models import Shot, StageResult
from rushbase_server.services import ai_results
from rushbase_server.services.evaluation import (
    MISSING,
    compute_metrics,
    predictions_from_outputs,
    render_markdown,
)
from support import Server

ServerFactory = Callable[..., AbstractAsyncContextManager[Server]]

FIXTURE = [
    ("shot_size", "wide", "wide"),
    ("shot_size", "wide", "wide"),
    ("shot_size", "wide", "medium"),
    ("shot_size", "close_up", "close_up"),
    ("shot_size", "medium", MISSING),
    ("camera_angle", "low", "low"),
    ("camera_angle", "high", "low"),
]


def test_metrics_regression_on_fixture() -> None:
    metrics = compute_metrics(FIXTURE)
    size = metrics["fields"]["shot_size"]
    assert size["n"] == 5
    assert size["accuracy"] == 0.6
    assert size["classes"]["wide"] == {"precision": 1.0, "recall": 0.6667, "f1": 0.8, "support": 3}
    assert size["classes"]["medium"]["recall"] == 0.0
    assert size["confusion"]["wide"] == {"medium": 1, "wide": 2}
    assert size["confusion"]["medium"] == {MISSING: 1}
    assert size["macro_f1"] == pytest.approx(0.6, abs=1e-3)
    angle = metrics["fields"]["camera_angle"]
    assert angle["classes"]["low"]["precision"] == 0.5
    assert metrics["overall"] == {"n": 7, "accuracy": round(4 / 7, 4)}


def test_empty_golden_set_is_not_an_error_and_report_renders() -> None:
    assert compute_metrics([]) == {"overall": {"n": 0, "accuracy": None}, "fields": {}}
    from datetime import UTC, datetime

    text = render_markdown(
        compute_metrics(FIXTURE), {"vision": ["m"]}, datetime(2026, 1, 1, tzinfo=UTC), 7
    )
    assert "| shot_size | 5 | 0.6 |" in text
    assert "- wide: medium x1, wide x2" in text


def test_predictions_read_the_ai_outputs_not_the_edited_columns() -> None:
    vision = vision_output([description(0, "x", shot_size=estimate("close_up"))])
    classify = classify_output([tag("harbour", 0)])
    found = predictions_from_outputs(
        vision.model_dump(mode="json"), classify.model_dump(mode="json")
    )
    assert found[(0, "shot_size")] == "close_up"
    assert found[(0, "location")] == "harbour"
    assert found[(0, "time_of_day")] == MISSING


@pytest.mark.postgres
async def test_golden_labelling_and_eval_run(
    server_factory: ServerFactory, database_url: str, library_root: Path
) -> None:
    async with server_factory(database_url, library_root=library_root) as server:
        admin = await server.setup_admin()
        async with server.runtime.session_factory() as session:
            await seed_taxonomy(session)
            asset = await seed_shots_asset(session, shots=1)
            vision = vision_output([description(0, "x", shot_size=estimate("wide"))])
            classify = classify_output([tag("harbour", 0)])
            for stage, out in (("vision", vision), ("classify", classify)):
                job = fake_job(asset.id, stage)
                await ai_results.apply_output(session, job, out)
                session.add(
                    StageResult(
                        asset_id=asset.id,
                        stage=stage,
                        ok=True,
                        output=out.model_dump(mode="json", by_alias=True),
                        finished_at=server.clock.now(),
                    )
                )
            shot_id = (await session.execute(select(Shot.id))).scalar_one()
            await session.commit()
        fields = (await server.client.get("/api/v1/golden/fields")).json()
        assert [f["key"] for f in fields][:2] == ["shot_size", "camera_angle"]
        assert "harbour" in next(f for f in fields if f["key"] == "location")["values"]
        queue = (await server.client.get("/api/v1/golden/items")).json()
        assert queue["total"] == 1
        target = queue["items"][0]["targets"][0]
        assert target["shot_id"] == str(shot_id)
        assert target["ai"]["shot_size"] == "wide"
        for field, value in (("shot_size", "close_up"), ("camera_angle", "eye_level"),
                             ("location", "harbour")):  # fmt: skip
            put = await server.client.put(
                "/api/v1/golden/labels",
                json={"asset_id": str(asset.id), "shot_id": str(shot_id), "field": field,
                      "expected": value},
                headers=admin,
            )  # fmt: skip
            assert put.status_code == 204, put.text
        bad = await server.client.put(
            "/api/v1/golden/labels",
            json={"asset_id": str(asset.id), "field": "shot_size", "expected": "enormous"},
            headers=admin,
        )
        assert bad.status_code == 422
        assert (await server.client.get("/api/v1/golden/items")).json()["total"] == 0
        stats = (await server.client.get("/api/v1/golden/stats")).json()
        assert stats["labels"] == 3
        run = (
            await server.client.post("/api/v1/admin/eval", json={"label": "t"}, headers=admin)
        ).json()
        metrics = run["metrics"]["fields"]
        assert metrics["shot_size"]["accuracy"] == 0.0
        assert metrics["camera_angle"]["accuracy"] == 1.0
        assert metrics["location"]["accuracy"] == 1.0
        assert run["model_refs"]["vision"] == ["test-model"]
        assert len((await server.client.get("/api/v1/eval/runs")).json()) == 1
