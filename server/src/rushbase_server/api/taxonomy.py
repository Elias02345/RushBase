"""Taxonomy administration, proposals and the worker snapshot."""

from collections.abc import Awaitable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from rushbase_server.api.deps import (
    AdminDep,
    DbDep,
    EditorDep,
    Principal,
    RuntimeDep,
    ViewerDep,
    WorkerDep,
    client_ip,
)
from rushbase_server.models import Asset, AssetFile, TaxField, TaxValue
from rushbase_server.services import audit, catalog, taxonomy
from rushbase_server.services.settings_store import set_setting

router = APIRouter(prefix="/api/v1", tags=["taxonomy"])

KEY_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"


class ValueOut(BaseModel):
    """A taxonomy value."""

    id: UUID
    field_id: UUID
    value: str
    label_de: str | None
    label_en: str | None
    synonyms: list[str]
    parent_id: UUID | None
    status: str
    description: str
    proposal_count: int
    asset_count: int


class FieldOut(BaseModel):
    """A taxonomy field with its values."""

    id: UUID
    key: str
    label_de: str
    label_en: str
    kind: str
    description: str
    sort: int
    values: list[ValueOut]


class TaxonomyOut(BaseModel):
    """The whole vocabulary."""

    version: int
    fields: list[FieldOut]


class FieldIn(BaseModel):
    """New field."""

    key: str = Field(pattern=KEY_PATTERN)
    label_de: str = Field(min_length=1, max_length=100)
    label_en: str = Field(min_length=1, max_length=100)
    kind: Literal["single", "multi"] = "multi"
    description: str = Field(default="", max_length=1000)
    sort: int = 0


class FieldPatch(BaseModel):
    """Field changes (the key is immutable)."""

    label_de: str | None = Field(default=None, min_length=1, max_length=100)
    label_en: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["single", "multi"] | None = None
    description: str | None = Field(default=None, max_length=1000)
    sort: int | None = None


class ValueIn(BaseModel):
    """New value; editors create proposals, admins approved values."""

    value: str = Field(min_length=1, max_length=100)
    label_de: str | None = Field(default=None, max_length=100)
    label_en: str | None = Field(default=None, max_length=100)
    synonyms: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=1000)
    parent_id: UUID | None = None


class ValuePatch(BaseModel):
    """Value changes; renaming keeps the old name as a synonym."""

    value: str | None = Field(default=None, min_length=1, max_length=100)
    label_de: str | None = Field(default=None, max_length=100)
    label_en: str | None = Field(default=None, max_length=100)
    synonyms: list[str] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=1000)
    parent_id: UUID | None = None


class MergeIn(BaseModel):
    """Merge target."""

    target_id: UUID


class SynonymIn(BaseModel):
    """A synonym to add."""

    synonym: str = Field(min_length=1, max_length=100)


class ExampleOut(BaseModel):
    """An asset that triggered a proposal."""

    asset_id: UUID
    filename: str
    poster_url: str | None


class ProposalOut(ValueOut):
    """A proposed value with the field label and example assets."""

    field_key: str
    field_label_de: str
    field_label_en: str
    examples: list[ExampleOut]


class FlagOut(BaseModel):
    """A review flag the VLM checks."""

    key: str = Field(pattern=KEY_PATTERN)
    description_de: str = ""
    description_en: str = ""


class WorkerValue(BaseModel):
    """Approved value in the worker snapshot."""

    value: str
    synonyms: list[str]
    description: str


class WorkerField(BaseModel):
    """Field in the worker snapshot."""

    key: str
    label_de: str
    label_en: str
    kind: str
    values: list[WorkerValue]


class WorkerTaxonomy(BaseModel):
    """What a worker needs for ``vision`` and ``classify``."""

    version: int
    fields: list[WorkerField]
    flags: list[FlagOut]


def _value_out(row: TaxValue, counts: dict[UUID, int]) -> ValueOut:
    return ValueOut(
        id=row.id,
        field_id=row.field_id,
        value=row.value,
        label_de=row.label_de,
        label_en=row.label_en,
        synonyms=list(row.synonyms),
        parent_id=row.parent_id,
        status=row.status,
        description=row.description,
        proposal_count=row.proposal_count,
        asset_count=counts.get(row.id, 0),
    )


def _actor(principal: Principal, request: Request) -> taxonomy.Actor:
    return taxonomy.Actor(principal.user.id, client_ip(request))


async def _guard[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except taxonomy.TaxonomyError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/taxonomy", response_model=TaxonomyOut, summary="Vocabulary with all values")
async def get_taxonomy(_: ViewerDep, db: DbDep) -> TaxonomyOut:
    """Fields and values of every status, with the number of tagged assets."""
    counts = await taxonomy.tag_counts(db)
    return TaxonomyOut(
        version=await taxonomy.current_version(db),
        fields=[
            FieldOut(
                id=f.id,
                key=f.key,
                label_de=f.label_de,
                label_en=f.label_en,
                kind=f.kind,
                description=f.description,
                sort=f.sort,
                values=[_value_out(v, counts) for v in values],
            )
            for f, values in await taxonomy.fields_with_values(db)
        ],
    )


@router.post("/taxonomy/fields", response_model=FieldOut, status_code=201)
async def create_field(body: FieldIn, principal: AdminDep, request: Request, db: DbDep) -> FieldOut:
    """Create a field."""
    field = await _guard(taxonomy.create_field(db, _actor(principal, request), **body.model_dump()))
    await db.commit()
    return FieldOut(**_field_dict(field), values=[])


def _field_dict(field: TaxField) -> dict[str, Any]:
    return {
        "id": field.id,
        "key": field.key,
        "label_de": field.label_de,
        "label_en": field.label_en,
        "kind": field.kind,
        "description": field.description,
        "sort": field.sort,
    }


@router.patch("/taxonomy/fields/{field_id}", response_model=FieldOut)
async def patch_field(
    field_id: UUID, body: FieldPatch, principal: AdminDep, request: Request, db: DbDep,
    runtime: RuntimeDep,
) -> FieldOut:  # fmt: skip
    """Change labels, kind, description or order of a field."""
    field = await _guard(
        taxonomy.update_field(
            db,
            _actor(principal, request),
            field_id,
            body.model_dump(exclude_unset=True, exclude_none=True),
            runtime.clock.now(),
            runtime.queue_config,
        )
    )
    await db.commit()
    return FieldOut(**_field_dict(field), values=[])


@router.delete("/taxonomy/fields/{field_id}", status_code=204)
async def delete_field(field_id: UUID, principal: AdminDep, request: Request, db: DbDep) -> None:
    """Delete an empty field."""
    await _guard(taxonomy.delete_field(db, _actor(principal, request), field_id))
    await db.commit()


@router.post("/taxonomy/fields/{field_id}/values", response_model=ValueOut, status_code=201)
async def create_value(
    field_id: UUID, body: ValueIn, principal: EditorDep, request: Request, db: DbDep,
    runtime: RuntimeDep,
) -> ValueOut:  # fmt: skip
    """Add a value: approved when an admin adds it, a proposal when an editor does."""
    row = await _guard(
        taxonomy.create_value(
            db,
            _actor(principal, request),
            field_id,
            **body.model_dump(),
            approved=principal.user.role == "admin",
            now=runtime.clock.now(),
            config=runtime.queue_config,
        )
    )
    await db.commit()
    return _value_out(row, {})


@router.patch("/taxonomy/values/{value_id}", response_model=ValueOut)
async def patch_value(
    value_id: UUID, body: ValuePatch, principal: AdminDep, request: Request, db: DbDep,
    runtime: RuntimeDep,
) -> ValueOut:  # fmt: skip
    """Rename, relabel, describe or re-parent a value."""
    row = await _guard(
        taxonomy.update_value(
            db,
            _actor(principal, request),
            value_id,
            body.model_dump(exclude_unset=True),
            runtime.clock.now(),
            runtime.queue_config,
        )
    )
    await db.commit()
    return _value_out(row, {})


@router.post("/taxonomy/values/{value_id}/approve", response_model=ValueOut)
async def approve_value(
    value_id: UUID, principal: EditorDep, request: Request, db: DbDep, runtime: RuntimeDep
) -> ValueOut:
    """Approve a proposal (bumps the taxonomy version, queues `classify`)."""
    row = await _guard(
        taxonomy.set_status(
            db, _actor(principal, request), value_id, "approved",
            runtime.clock.now(), runtime.queue_config,
        )
    )  # fmt: skip
    await db.commit()
    return _value_out(row, {})


@router.post("/taxonomy/values/{value_id}/deprecate", response_model=ValueOut)
async def deprecate_value(
    value_id: UUID, principal: AdminDep, request: Request, db: DbDep, runtime: RuntimeDep
) -> ValueOut:
    """Retire a value; existing tags stay but it leaves the snapshot and the facets."""
    row = await _guard(
        taxonomy.set_status(
            db, _actor(principal, request), value_id, "deprecated",
            runtime.clock.now(), runtime.queue_config,
        )
    )  # fmt: skip
    await db.commit()
    return _value_out(row, {})


@router.post("/taxonomy/values/{value_id}/merge", response_model=ValueOut)
async def merge_value(
    value_id: UUID, body: MergeIn, principal: AdminDep, request: Request, db: DbDep,
    runtime: RuntimeDep,
) -> ValueOut:  # fmt: skip
    """Merge a value into another value of the same field."""
    row = await _guard(
        taxonomy.merge_values(
            db, _actor(principal, request), value_id, body.target_id,
            runtime.clock.now(), runtime.queue_config,
        )
    )  # fmt: skip
    await db.commit()
    return _value_out(row, {})


@router.post("/taxonomy/values/{value_id}/synonyms", response_model=ValueOut)
async def add_synonym(
    value_id: UUID, body: SynonymIn, principal: AdminDep, request: Request, db: DbDep,
    runtime: RuntimeDep,
) -> ValueOut:  # fmt: skip
    """Add a synonym to a value."""
    row = await _guard(
        taxonomy.add_synonym(
            db, _actor(principal, request), value_id, body.synonym,
            runtime.clock.now(), runtime.queue_config,
        )
    )  # fmt: skip
    await db.commit()
    return _value_out(row, {})


@router.get("/taxonomy/proposals", response_model=list[ProposalOut])
async def list_proposals(_: EditorDep, db: DbDep) -> list[ProposalOut]:
    """Values the AI proposed, most frequent first, with example assets."""
    rows = (
        await db.execute(
            select(TaxValue, TaxField)
            .join(TaxField, TaxField.id == TaxValue.field_id)
            .where(TaxValue.status == "proposed")
            .order_by(TaxValue.proposal_count.desc(), TaxValue.value)
            .limit(500)
        )
    ).all()
    asset_ids = {a for v, _f in rows for a in v.example_asset_ids}
    assets = {
        a.id: (a, name)
        for a, name in (
            await db.execute(
                select(Asset, AssetFile.rel_path)
                .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
                .where(Asset.id.in_(asset_ids))
            )
        ).all()
    }
    result = []
    for value, field in rows:
        examples = [
            ExampleOut(
                asset_id=a.id,
                filename=(assets[a.id][1] or "").rsplit("/", 1)[-1],
                poster_url=catalog.derived_links(a)["poster_url"],
            )
            for a in (assets[i][0] for i in value.example_asset_ids if i in assets)
        ]
        result.append(
            ProposalOut(
                **_value_out(value, {}).model_dump(),
                field_key=field.key,
                field_label_de=field.label_de,
                field_label_en=field.label_en,
                examples=examples,
            )
        )
    return result


@router.get("/taxonomy/flags", response_model=list[FlagOut])
async def get_flags(_: ViewerDep, db: DbDep) -> list[FlagOut]:
    """Review flags the VLM checks for every shot."""
    return [FlagOut(**f) for f in (await taxonomy.get_flags(db))]


@router.put("/taxonomy/flags", response_model=list[FlagOut])
async def put_flags(
    body: list[FlagOut], principal: AdminDep, request: Request, db: DbDep, runtime: RuntimeDep
) -> list[FlagOut]:
    """Replace the flag list (bumps the taxonomy version so workers refetch it)."""
    await set_setting(db, taxonomy.FLAGS_KEY, [f.model_dump() for f in body], principal.user.id)
    audit.record(
        db,
        actor_kind="user",
        actor_id=principal.user.id,
        action="taxonomy.flags",
        details={"keys": [f.key for f in body]},
        ip=client_ip(request),
    )
    await taxonomy.bump_version(db)
    await db.commit()
    return body


@router.get(
    "/worker/taxonomy",
    response_model=WorkerTaxonomy,
    tags=["worker"],
    summary="Approved vocabulary and review flags",
)
async def worker_taxonomy(_: WorkerDep, db: DbDep) -> WorkerTaxonomy:
    """Snapshot for `vision` and `classify`: approved values only, with the version number."""
    return WorkerTaxonomy.model_validate(await taxonomy.worker_snapshot(db))
