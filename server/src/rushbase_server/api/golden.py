"""Golden set labelling and evaluation runs."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Text, cast, func, select

from rushbase_core import results_ai
from rushbase_server.api.catalog import AssetSummary, FiltersDep, _summary
from rushbase_server.api.deps import AdminDep, DbDep, EditorDep, ViewerDep
from rushbase_server.models import (
    Asset,
    AssetFile,
    Batch,
    EvalRun,
    GoldenLabel,
    Shot,
    TaxField,
    TaxValue,
)
from rushbase_server.services import audit, catalog, derived, evaluation, golden

router = APIRouter(prefix="/api/v1", tags=["golden"])

CORE_ENUMS: dict[str, type[StrEnum]] = {
    "shot_size": results_ai.ShotSize,
    "camera_angle": results_ai.CameraAngle,
    "interior_exterior": results_ai.InteriorExterior,
    "time_of_day": results_ai.TimeOfDay,
    "person_identifiable": results_ai.Tri,
}
CORE_LABELS = {
    "shot_size": ("Einstellungsgröße", "Shot size"),
    "camera_angle": ("Kamerawinkel", "Camera angle"),
    "interior_exterior": ("Innen/Außen", "Interior/exterior"),
    "time_of_day": ("Tageszeit", "Time of day"),
    "people_count": ("Personenzahl", "People count"),
    "person_identifiable": ("Person erkennbar", "Person identifiable"),
}
PEOPLE_COUNTS = [str(n) for n in range(0, 7)] + ["7+"]


class GoldenFieldOut(BaseModel):
    """A field that can be labelled, with its allowed values."""

    key: str
    label_de: str
    label_en: str
    kind: str
    values: list[str]
    multi: bool = False


class GoldenTarget(BaseModel):
    """What gets labelled: the asset (photos) or one shot (videos)."""

    shot_id: UUID | None
    idx: int | None
    start_ms: int | None
    end_ms: int | None
    keyframe_urls: list[str]
    ai: dict[str, str | None]
    labels: dict[str, str]


class GoldenItem(BaseModel):
    """An asset with its label targets."""

    asset: AssetSummary
    targets: list[GoldenTarget]


class GoldenPage(BaseModel):
    """Page of items to label."""

    items: list[GoldenItem]
    total: int


class LabelIn(BaseModel):
    """Set the expected value of a field."""

    asset_id: UUID
    shot_id: UUID | None = None
    field: str = Field(min_length=1, max_length=40)
    expected: str = Field(min_length=1, max_length=100)


class LabelRef(BaseModel):
    """Identify a label."""

    asset_id: UUID
    shot_id: UUID | None = None
    field: str


class GoldenStats(BaseModel):
    """Label counts."""

    labels: int
    assets: int
    corrections: int
    by_field: dict[str, int]


class EvalRunOut(BaseModel):
    """A stored evaluation run."""

    id: UUID
    created_at: datetime
    label: str | None
    golden_count: int
    model_refs: dict[str, Any]
    metrics: dict[str, Any]


class EvalIn(BaseModel):
    """Optional label for the run."""

    label: str | None = Field(default=None, max_length=100)


async def _fields(db: DbDep) -> list[GoldenFieldOut]:
    out = [
        GoldenFieldOut(
            key=key,
            label_de=CORE_LABELS[key][0],
            label_en=CORE_LABELS[key][1],
            kind="core",
            values=[m.value for m in CORE_ENUMS[key]] if key in CORE_ENUMS else PEOPLE_COUNTS,
        )
        for key in golden.CORE_FIELDS
    ]
    for tax_field, values in await _approved(db):
        out.append(
            GoldenFieldOut(
                key=tax_field.key,
                label_de=tax_field.label_de,
                label_en=tax_field.label_en,
                kind="taxonomy",
                values=[v.value for v in values],
                multi=tax_field.kind == "multi",
            )
        )
    return out


async def _approved(db: DbDep) -> list[tuple[TaxField, list[TaxValue]]]:
    from rushbase_server.services import taxonomy

    return [(f, v) for f, v in await taxonomy.fields_with_values(db, ("approved",)) if v]


@router.get("/golden/fields", response_model=list[GoldenFieldOut])
async def golden_fields(_: ViewerDep, db: DbDep) -> list[GoldenFieldOut]:
    """Labelable fields: the core attributes and every taxonomy field with approved values."""
    return await _fields(db)


def _keyframes(asset: Asset, shot: Shot) -> list[str]:
    ordered = sorted(shot.keyframes, key=lambda k: ["start", "middle", "end"].index(k["position"]))
    return [u for k in ordered if (u := derived.derived_url(asset.id, k["rel_path"]))]


def _ai_values(holder: Asset | Shot) -> dict[str, str | None]:
    column = {"camera_angle": "angle"} if isinstance(holder, Shot) else {}
    return {
        key: (None if (v := getattr(holder, column.get(key, key))) is None else str(v))
        for key in golden.CORE_FIELDS
    }


@router.get("/golden/items", response_model=GoldenPage, summary="Assets to label")
async def golden_items(
    _: EditorDep,
    db: DbDep,
    filters: FiltersDep,
    unlabeled: bool = True,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> GoldenPage:
    """Filed assets in a stable pseudo-random order (so labelling samples the whole library)."""
    labelled = select(GoldenLabel.asset_id).distinct().scalar_subquery()
    base = catalog.apply_filters(
        select(Asset, AssetFile.rel_path, Batch.label)
        .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
        .outerjoin(Batch, Batch.id == Asset.batch_id)
        .where(Asset.media_type.in_(("video", "photo"))),
        filters,
    )
    if unlabeled:
        base = base.where(Asset.id.notin_(labelled))
    total = int(
        await db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    )
    rows = (
        await db.execute(base.order_by(func.md5(cast(Asset.id, Text))).offset(offset).limit(limit))
    ).all()
    items = []
    for asset, path, label in rows:
        shots = (
            (await db.execute(select(Shot).where(Shot.asset_id == asset.id).order_by(Shot.idx)))
            .scalars()
            .all()
        )
        labels = await golden.labels_for(db, asset.id)
        by_shot: dict[UUID | None, dict[str, str]] = {}
        for item in labels:
            by_shot.setdefault(item.shot_id, {})[item.field] = item.expected
        links = catalog.derived_links(asset)
        targets = (
            [
                GoldenTarget(
                    shot_id=s.id,
                    idx=s.idx,
                    start_ms=s.start_ms,
                    end_ms=s.end_ms,
                    keyframe_urls=_keyframes(asset, s),
                    ai=_ai_values(s),
                    labels=by_shot.get(s.id, {}),
                )
                for s in shots
            ]
            if shots
            else [
                GoldenTarget(
                    shot_id=None,
                    idx=None,
                    start_ms=None,
                    end_ms=None,
                    keyframe_urls=[links["poster_url"]] if links["poster_url"] else [],
                    ai=_ai_values(asset),
                    labels=by_shot.get(None, {}),
                )
            ]
        )
        items.append(
            GoldenItem(asset=AssetSummary(**_summary(asset, path, label)), targets=targets)
        )
    return GoldenPage(items=items, total=total)


@router.put("/golden/labels", status_code=status.HTTP_204_NO_CONTENT)
async def put_label(body: LabelIn, principal: EditorDep, db: DbDep) -> None:
    """Store the expected value of a field (replaces an earlier label)."""
    fields = {f.key: f for f in await _fields(db)}
    spec = fields.get(body.field)
    if spec is None or body.expected not in spec.values:
        raise HTTPException(422, "unknown field or value")
    if await db.get(Asset, body.asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    await golden.upsert_label(
        db, body.asset_id, body.shot_id, body.field, body.expected, principal.user.id
    )
    await db.commit()


@router.delete("/golden/labels", status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(body: LabelRef, _: EditorDep, db: DbDep) -> None:
    """Remove a label."""
    await golden.delete_label(db, body.asset_id, body.shot_id, body.field)
    await db.commit()


@router.get("/golden/stats", response_model=GoldenStats)
async def golden_stats(_: ViewerDep, db: DbDep) -> GoldenStats:
    """How many labels exist, per field, and how many came from human corrections."""
    rows = await db.execute(select(GoldenLabel.field, func.count()).group_by(GoldenLabel.field))
    per_field = {field: count for field, count in rows}  # noqa: C416
    corrections = await db.scalar(
        select(func.count()).select_from(GoldenLabel).where(GoldenLabel.origin == "correction")
    )
    assets = await db.scalar(select(func.count(func.distinct(GoldenLabel.asset_id))))
    return GoldenStats(
        labels=sum(per_field.values()),
        assets=int(assets or 0),
        corrections=int(corrections or 0),
        by_field=per_field,
    )


def _run_out(run: EvalRun) -> EvalRunOut:
    return EvalRunOut(
        id=run.id,
        created_at=run.created_at,
        label=run.label,
        golden_count=run.golden_count,
        model_refs=run.model_refs,
        metrics=run.metrics,
    )


@router.post("/admin/eval", response_model=EvalRunOut, tags=["admin"])
async def run_eval(body: EvalIn, principal: AdminDep, db: DbDep) -> EvalRunOut:
    """Evaluate the latest AI results against the golden labels and store the run."""
    run = await evaluation.run_evaluation(db, body.label)
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="eval.run",
        target_kind="eval_run",
        target_id=run.id,
        details={"golden": run.golden_count},
    )
    await db.commit()
    await db.refresh(run)
    return _run_out(run)


@router.get("/eval/runs", response_model=list[EvalRunOut])
async def list_runs(_: ViewerDep, db: DbDep) -> list[EvalRunOut]:
    """The latest 50 evaluation runs, newest first."""
    rows = await db.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(50))
    return [_run_out(r) for r in rows.scalars()]
