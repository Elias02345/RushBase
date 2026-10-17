"""Filing rules, quarantine and library settings (path mapping, central sidecar copy)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from rushbase_server.api.deps import AdminDep, DbDep, EditorDep, RuntimeDep
from rushbase_server.services import audit, filing_rules, quarantine, trash
from rushbase_server.services.settings_store import (
    CENTRAL_META_KEY,
    DEFAULT_TRASH_RETENTION_DAYS,
    PATH_ROOTS_KEY,
    TRASH_RETENTION_KEY,
    get_setting,
    set_setting,
)

router = APIRouter(prefix="/api/v1", tags=["filing"])


class RuleOut(BaseModel):
    """A filing rule version."""

    id: UUID
    version: int
    template: str
    fallbacks: dict[str, Any]
    active: bool
    note: str
    created_at: datetime


class RuleIn(BaseModel):
    """A new rule version (created inactive)."""

    template: str = Field(min_length=1, max_length=500)
    fallbacks: dict[str, str] = Field(default_factory=dict)
    note: str = Field(default="", max_length=500)


class DryRunIn(BaseModel):
    """Evaluate a candidate template for the newest ``limit`` assets."""

    template: str = Field(min_length=1, max_length=500)
    fallbacks: dict[str, str] = Field(default_factory=dict)
    limit: int = Field(default=200, ge=1, le=1000)


class DryRunItemOut(BaseModel):
    """Computed location of one asset."""

    asset_id: UUID
    filename: str
    current_dir: str | None
    path: str
    warnings: list[str]
    conflict: bool


class DryRunOut(BaseModel):
    """Dry-run result: every asset, plus the conflicts and distinct warnings."""

    items: list[DryRunItemOut]
    conflicts: list[DryRunItemOut]
    warnings: list[str]
    directories: int


def _rule(rule: Any) -> RuleOut:
    return RuleOut(
        id=rule.id,
        version=rule.version,
        template=rule.template,
        fallbacks=rule.fallbacks,
        active=rule.active,
        note=rule.note,
        created_at=rule.created_at,
    )


@router.get("/filing-rules", response_model=list[RuleOut], summary="List filing rule versions")
async def list_rules(_: EditorDep, db: DbDep) -> list[RuleOut]:
    """All versions, newest first; the default rule is seeded on first use."""
    rules = await filing_rules.list_rules(db)
    await db.commit()
    return [_rule(r) for r in rules]


@router.post(
    "/filing-rules",
    response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a rule version",
)
async def create_rule(body: RuleIn, editor: EditorDep, db: DbDep) -> RuleOut:
    """Validate and store a new, inactive version. Activate it after a dry-run."""
    try:
        rule = await filing_rules.create_rule(
            db, body.template, body.fallbacks, body.note, editor.user.id
        )
    except filing_rules.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return _rule(rule)


@router.post("/filing-rules/dry-run", response_model=DryRunOut, summary="Dry-run a template")
async def dry_run(body: DryRunIn, _: EditorDep, runtime: RuntimeDep, db: DbDep) -> DryRunOut:
    """Show where the latest assets would be filed, with conflicts and warnings."""
    try:
        results = await filing_rules.dry_run(
            db,
            runtime.binding.root,
            body.template,
            body.fallbacks,
            body.limit,
            runtime.settings.filing_path_budget,
        )
    except filing_rules.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc
    items = [
        DryRunItemOut(
            asset_id=r.asset_id,
            filename=r.filename,
            current_dir=r.current_dir,
            path=r.path,
            warnings=list(r.warnings),
            conflict=r.conflict,
        )
        for r in results
    ]
    return DryRunOut(
        items=items,
        conflicts=[i for i in items if i.conflict],
        warnings=sorted({w for i in items for w in i.warnings}),
        directories=len({i.path for i in items}),
    )


@router.post("/filing-rules/{rule_id}/activate", response_model=RuleOut, summary="Activate a rule")
async def activate_rule(rule_id: UUID, editor: EditorDep, db: DbDep) -> RuleOut:
    """Make the version active. Only assets filed afterwards are affected."""
    try:
        rule = await filing_rules.activate_rule(db, rule_id, editor.user.id)
    except filing_rules.RuleError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await db.commit()
    return _rule(rule)


# --- quarantine ----------------------------------------------------------------------------


class QuarantineItem(BaseModel):
    """A quarantined asset."""

    asset_id: UUID
    filename: str
    media_type: str
    size_total: int
    stage: str | None
    reason: str | None
    at: str | None
    moved: bool


@router.get("/quarantine", response_model=list[QuarantineItem], summary="List quarantined assets")
async def list_quarantine(_: EditorDep, db: DbDep) -> list[QuarantineItem]:
    """Assets whose processing failed permanently, newest first."""
    return [
        QuarantineItem(
            asset_id=a.id,
            filename=name,
            media_type=a.media_type,
            size_total=a.size_total,
            stage=(a.quarantine or {}).get("stage"),
            reason=(a.quarantine or {}).get("reason"),
            at=(a.quarantine or {}).get("at"),
            moved=a.quarantine is not None,
        )
        for a, name in await quarantine.list_quarantined(db)
    ]


@router.post(
    "/quarantine/{asset_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Move back to Input and reprocess",
)
async def retry_quarantine(
    asset_id: UUID, editor: EditorDep, runtime: RuntimeDep, db: DbDep
) -> None:
    """Move the files back to their original `Input/` location (if free) and requeue the jobs."""
    root = runtime.binding.root
    if root is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no library mounted")
    try:
        await quarantine.retry_asset(
            db,
            root,
            asset_id,
            await runtime.enabled_stages(),
            runtime.queue_config,
            runtime.clock.now(),
            editor.user.id,
        )
    except quarantine.QuarantineError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await db.commit()


# --- library settings ------------------------------------------------------------------------


class LibrarySettings(BaseModel):
    """Per-OS library roots (to build local paths) and sidecar options."""

    path_roots: dict[str, str | None] = Field(
        default_factory=dict, description="Keys: windows, macos, linux."
    )
    central_meta: bool = Field(
        default=False, description="Also write a copy of every sidecar to .rushbase/meta/."
    )
    trash_retention_days: int = Field(
        default=DEFAULT_TRASH_RETENTION_DAYS,
        ge=1,
        le=3650,
        description="Days a trashed asset is kept before it is deleted permanently.",
    )


@router.get("/admin/library-settings", response_model=LibrarySettings, tags=["admin"])
async def get_library_settings(_: AdminDep, db: DbDep) -> LibrarySettings:
    """Current path mapping and sidecar options."""
    return LibrarySettings(
        path_roots=await get_setting(db, PATH_ROOTS_KEY, {}),
        central_meta=bool(await get_setting(db, CENTRAL_META_KEY, False)),
        trash_retention_days=await trash.retention_days(db),
    )


@router.put("/admin/library-settings", response_model=LibrarySettings, tags=["admin"])
async def put_library_settings(
    body: LibrarySettings, admin: AdminDep, db: DbDep
) -> LibrarySettings:
    """Store the path mapping and sidecar options."""
    unknown = set(body.path_roots) - {"windows", "macos", "linux"}
    if unknown:
        raise HTTPException(
            422,
            f"unknown platform(s): {', '.join(sorted(unknown))}",
        )
    roots = {k: v for k, v in body.path_roots.items() if v}
    await set_setting(db, PATH_ROOTS_KEY, roots, admin.user.id)
    await set_setting(db, CENTRAL_META_KEY, body.central_meta, admin.user.id)
    await set_setting(db, TRASH_RETENTION_KEY, body.trash_retention_days, admin.user.id)
    audit.record(db, actor_kind="user", actor_id=admin.user.id, action="library_settings.update")
    await db.commit()
    return body.model_copy(update={"path_roots": roots})
