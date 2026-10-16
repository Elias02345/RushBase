"""Human edits of AI results: columns, tags, review state, lock, consent.

Edits are source ``human`` (highest precedence): edited columns are recorded in ``human_fields``
so reprocessing never overwrites them, removed tags are remembered as rejected, and a correction
of a core AI value is copied to the golden set (setting ``golden.from_corrections``).
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, AssetTag, Shot, TaxField, TaxValue
from rushbase_server.services import audit, golden

ASSET_COLUMNS = (
    "title",
    "rating",
    "locked",
    "consent",
    "consent_ref",
    "person_identifiable",
    "summary_short",
    "summary_short_en",
    "summary_long_de",
    "summary_long_en",
    "shot_size",
    "camera_angle",
    "interior_exterior",
    "time_of_day",
    "people_count",
)
SHOT_COLUMNS = (
    "description_short_de",
    "description_short_en",
    "description_long_de",
    "description_long_en",
    "shot_size",
    "camera_angle",
    "interior_exterior",
    "time_of_day",
    "people_count",
    "person_identifiable",
)
SHOT_COLUMN_NAMES = {"camera_angle": "angle"}
"""Public field name -> shot column, where they differ."""
NOT_AI_FIELDS = frozenset({"title", "rating", "locked", "consent", "consent_ref"})
REJECTED_KEY = "rejected_tags"


class EditError(Exception):
    """An edit is invalid; ``status`` is the suggested HTTP status."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class TagRef:
    """A tag by field key and value."""

    field: str
    value: str


@dataclass
class Edit:
    """A set of changes to one asset or shot."""

    columns: dict[str, Any] = field(default_factory=dict)
    add_tags: list[TagRef] = field(default_factory=list)
    remove_tags: list[TagRef] = field(default_factory=list)
    review_done: bool = False
    source: str = "human"
    """``human`` (highest precedence) or ``claude`` (MCP: never overrides or removes human tags)."""


def _rejected(asset: Asset) -> list[str]:
    return list(asset.ai_details.get(REJECTED_KEY, []))


def _set_rejected(asset: Asset, items: list[str]) -> None:
    asset.ai_details = {**asset.ai_details, REJECTED_KEY: sorted(set(items))}


def rejected_key(field_key: str, value: str, shot_idx: int | None) -> str:
    """Identifier of a removed AI tag (``classify`` skips it on reprocessing)."""
    return f"{field_key}:{value.lower()}:{'' if shot_idx is None else shot_idx}"


async def _resolve(session: AsyncSession, ref: TagRef) -> tuple[TaxField, TaxValue]:
    row = (
        await session.execute(
            select(TaxField, TaxValue)
            .join(TaxValue, TaxValue.field_id == TaxField.id)
            .where(TaxField.key == ref.field)
            .where(TaxValue.value.ilike(ref.value, escape="\\"))
        )
    ).first()
    if row is None or row[1].status == "deprecated":
        raise EditError(f"unknown or deprecated tag {ref.field}:{ref.value}")
    return row[0], row[1]


def _golden_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


async def edit_target(
    session: AsyncSession,
    actor_id: UUID,
    ip: str | None,
    asset: Asset,
    shot: Shot | None,
    edit: Edit,
) -> dict[str, Any]:
    """Apply ``edit`` to the asset (``shot`` is ``None``) or to one of its shots.

    Returns the audit details (changed columns, added/removed tags).
    """
    allowed = SHOT_COLUMNS if shot is not None else ASSET_COLUMNS
    unknown = set(edit.columns) - set(allowed)
    if unknown:
        raise EditError(f"cannot edit: {', '.join(sorted(unknown))}")
    target: Asset | Shot = shot if shot is not None else asset
    changed: dict[str, Any] = {}
    human = edit.source == "human"
    enabled = human and await golden.corrections_enabled(session)
    for name, value in edit.columns.items():
        column = SHOT_COLUMN_NAMES.get(name, name) if shot is not None else name
        before = getattr(target, column)
        if before == value:
            continue
        setattr(target, column, value)
        changed[name] = {"from": before, "to": value}
        if human and name not in NOT_AI_FIELDS and column not in human_list(target):
            target.human_fields = [*human_list(target), column]
        if enabled and name in golden.CORE_FIELDS and value is not None:
            await golden.upsert_label(
                session,
                asset.id,
                shot.id if shot else None,
                name,
                _golden_value(value),
                actor_id,
                "correction",
            )
    added, removed = await _edit_tags(session, asset, shot, edit, actor_id, enabled)
    if edit.review_done:
        asset.review_state = "done"
        changed["review_state"] = "done"
    details: dict[str, Any] = {"shot": shot.idx if shot else None, "columns": changed}
    if not human:
        details["source"] = edit.source
    if added or removed:
        details["tags_added"], details["tags_removed"] = added, removed
    if changed or added or removed:
        audit.record(
            session,
            actor_kind="user" if human else "mcp",
            actor_id=actor_id,
            action="asset.edit",
            target_kind="asset",
            target_id=asset.id,
            details=details,
            ip=ip,
        )
    return details


def human_list(target: Asset | Shot) -> list[str]:
    """Columns of ``target`` edited by a person."""
    return list(target.human_fields)


async def _edit_tags(
    session: AsyncSession,
    asset: Asset,
    shot: Shot | None,
    edit: Edit,
    actor_id: UUID,
    golden_enabled: bool,
) -> tuple[list[str], list[str]]:
    shot_id = shot.id if shot else None
    shot_idx = shot.idx if shot else None
    scope = and_(
        AssetTag.asset_id == asset.id,
        AssetTag.shot_id.is_(None) if shot_id is None else AssetTag.shot_id == shot_id,
    )
    rejected = _rejected(asset)
    added: list[str] = []
    removed: list[str] = []
    for ref in edit.remove_tags:
        tag_field, value = await _resolve(session, ref)
        removal = delete(AssetTag).where(scope, AssetTag.value_id == value.id)
        if edit.source != "human":
            removal = removal.where(AssetTag.source != "human")  # human tags are out of reach
        result = await session.execute(removal.returning(AssetTag.source))
        sources = list(result.scalars())
        if "ai" in sources or "claude" in sources:
            rejected.append(rejected_key(tag_field.key, value.value, shot_idx))
        if sources:
            removed.append(f"{ref.field}:{value.value}")
    for ref in edit.add_tags:
        tag_field, value = await _resolve(session, ref)
        if edit.source != "human" and await session.scalar(
            select(AssetTag.id).where(
                scope, AssetTag.source == "human", AssetTag.field_id == tag_field.id
            ).limit(1)
        ) is not None and tag_field.kind == "single":
            continue  # a person already decided this single-valued field
        if tag_field.kind == "single":
            clear = delete(AssetTag).where(scope, AssetTag.field_id == tag_field.id)
            if edit.source != "human":
                clear = clear.where(AssetTag.source != "human")
            await session.execute(clear)
        exists = await session.scalar(
            select(AssetTag.id).where(
                scope,
                AssetTag.value_id == value.id,
                AssetTag.source.in_(("human", edit.source)),
            )
        )
        if exists is None:
            session.add(
                AssetTag(
                    asset_id=asset.id,
                    shot_id=shot_id,
                    field_id=tag_field.id,
                    value_id=value.id,
                    source=edit.source,
                    confidence=1.0,
                )
            )
        key = rejected_key(tag_field.key, value.value, shot_idx)
        rejected = [r for r in rejected if r != key]
        added.append(f"{ref.field}:{value.value}")
        if golden_enabled and tag_field.kind == "single":
            await golden.upsert_label(
                session, asset.id, shot_id, tag_field.key, value.value, actor_id, "correction"
            )
    _set_rejected(asset, rejected)
    await session.flush()
    return added, removed
