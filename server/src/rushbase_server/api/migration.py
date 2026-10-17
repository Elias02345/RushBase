"""Content migration tool (administrators, web interface only, disabled by default)."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from rushbase_server.api.deps import DbDep, RuntimeDep, WebAdminDep
from rushbase_server.models import Asset
from rushbase_server.services import ai_sidecar, audit, filing_rules, migration
from rushbase_server.services.settings_store import (
    MIGRATION_ENABLED_KEY,
    PATH_ROOTS_KEY,
    get_setting,
    set_setting,
)

router = APIRouter(prefix="/api/v1/admin/migration", tags=["migration"])


class MigrationSettings(BaseModel):
    """Whether the tool is switched on."""

    enabled: bool = False


class Selection(BaseModel):
    """The assets to re-file (give ``asset_ids`` or ``batch_id``) and the new template."""

    asset_ids: list[UUID] = Field(default_factory=list, max_length=migration.MAX_ASSETS)
    batch_id: UUID | None = None
    template: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _one_selection(self) -> "Selection":
        if bool(self.asset_ids) == (self.batch_id is not None):
            raise ValueError("give either asset_ids or batch_id")
        return self


class ExecuteIn(Selection):
    """Execution needs the typed confirmation shown by the preview."""

    confirmation: str = Field(max_length=50)


class MigrationItemOut(BaseModel):
    """One unit of the preview."""

    asset_ids: list[UUID]
    filename: str
    old_dir: str
    new_dir: str
    warnings: list[str]
    skipped: str | None


class PreviewOut(BaseModel):
    """The dry run."""

    items: list[MigrationItemOut]
    to_move: int
    confirmation: str


class ExecuteOut(BaseModel):
    """The result of a run, with the relink list."""

    moved: int
    failed: int
    relink_csv: str
    relink_path: str


async def _require_enabled(db: DbDep) -> None:
    if not await get_setting(db, MIGRATION_ENABLED_KEY, False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "the migration tool is disabled")


async def _plans(
    body: Selection, runtime: RuntimeDep, db: DbDep
) -> tuple[Path, list[migration.UnitPlan]]:
    if runtime.binding.root is None or not runtime.binding.mounted:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no library mounted")
    ids = body.asset_ids
    if body.batch_id is not None:
        ids = list(
            (
                await db.execute(
                    select(Asset.id)
                    .where(Asset.batch_id == body.batch_id, Asset.status == "filed")
                    .limit(migration.MAX_ASSETS + 1)
                )
            )
            .scalars()
            .all()
        )
        if len(ids) > migration.MAX_ASSETS:
            raise HTTPException(422, f"more than {migration.MAX_ASSETS} assets in the batch")
    rule = await filing_rules.ensure_default_rule(db)
    try:
        plans = await migration.plan_units(
            db,
            runtime.binding.root,
            ids,
            body.template,
            rule.fallbacks,
            runtime.settings.filing_path_budget,
        )
    except filing_rules.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc
    return runtime.binding.root, plans


@router.get("/settings", response_model=MigrationSettings, summary="Migration tool switch")
async def get_settings(_: WebAdminDep, db: DbDep) -> MigrationSettings:
    """Whether the migration tool is enabled (default: off)."""
    return MigrationSettings(enabled=bool(await get_setting(db, MIGRATION_ENABLED_KEY, False)))


@router.put("/settings", response_model=MigrationSettings, summary="Switch the migration tool")
async def put_settings(body: MigrationSettings, admin: WebAdminDep, db: DbDep) -> MigrationSettings:
    """Enable or disable the tool."""
    await set_setting(db, MIGRATION_ENABLED_KEY, body.enabled, admin.user.id)
    audit.record(
        db,
        actor_kind="user",
        actor_id=admin.user.id,
        action="migration.enable" if body.enabled else "migration.disable",
    )
    await db.commit()
    return body


@router.post("/preview", response_model=PreviewOut, summary="Dry run of a migration")
async def preview(
    body: Selection, _admin: WebAdminDep, runtime: RuntimeDep, db: DbDep
) -> PreviewOut:
    """Show where the selected assets would move with the new template; moves nothing."""
    await _require_enabled(db)
    _, plans = await _plans(body, runtime, db)
    count = sum(len(p.assets) for p in plans if not p.skipped)
    return PreviewOut(
        items=[
            MigrationItemOut(
                **{**vars(i), "asset_ids": list(i.asset_ids), "warnings": list(i.warnings)}
            )
            for i in migration.preview_items(plans)
        ],
        to_move=count,
        confirmation=migration.expected_confirmation(count),
    )


@router.post("/execute", response_model=ExecuteOut, summary="Run a migration")
async def execute(
    body: ExecuteIn, admin: WebAdminDep, runtime: RuntimeDep, db: DbDep
) -> ExecuteOut:
    """Move the assets (rename only), journal it and return the relink list."""
    await _require_enabled(db)
    root, plans = await _plans(body, runtime, db)
    count = sum(len(p.assets) for p in plans if not p.skipped)
    if count == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "nothing to move")
    if body.confirmation.strip() != migration.expected_confirmation(count):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "confirmation does not match")
    roots = await get_setting(db, PATH_ROOTS_KEY, {})
    moved, failed, text, rel = await migration.execute(
        db, root, plans, roots, admin.user.id, runtime.clock.now()
    )
    for unit in plans:
        for asset in unit.assets if not unit.skipped else []:
            await ai_sidecar.refresh_for_runtime(runtime, db, asset.id)
    runtime.cache.clear()
    return ExecuteOut(moved=moved, failed=failed, relink_csv=text, relink_path=rel)
