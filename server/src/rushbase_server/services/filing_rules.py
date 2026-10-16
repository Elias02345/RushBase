"""Versioned filing rules: creation, activation, evaluation for an asset and dry-runs.

Rules are immutable once created; changing the layout means creating a new version and
activating it. Activation only affects assets that are filed afterwards (stable paths,
invariant 3).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core.filing import (
    DEFAULT_PATH_BUDGET,
    DEFAULT_TEMPLATE,
    FilingContext,
    Template,
    TemplateError,
    parse_template,
)
from rushbase_server.models import Asset, AssetFile, Batch, FilingRule
from rushbase_server.services import audit
from rushbase_server.services.normalize import local_capture_date
from rushbase_server.services.placement import DirectoryPlan, plan_directory


class RuleError(Exception):
    """A rule request is invalid (template problems, unknown rule)."""


def parse_rule_template(template: str) -> Template:
    """Parse ``template``; raises :class:`RuleError` with the reason."""
    try:
        return parse_template(template)
    except TemplateError as exc:
        raise RuleError(str(exc)) from exc


def clean_fallbacks(fallbacks: Mapping[str, Any]) -> dict[str, str]:
    """Validate per-field fallbacks (``{"capture_date": "undated"}``)."""
    cleaned: dict[str, str] = {}
    for key, value in fallbacks.items():
        if not isinstance(value, str) or not value.strip():
            raise RuleError(f"fallback for {key!r} must be a non-empty string")
        cleaned[str(key)] = value
    return cleaned


async def list_rules(session: AsyncSession) -> list[FilingRule]:
    """All rule versions, newest first (seeds the default rule on an empty table)."""
    await ensure_default_rule(session)
    return list(
        (await session.execute(select(FilingRule).order_by(FilingRule.version.desc()))).scalars()
    )


async def ensure_default_rule(session: AsyncSession) -> FilingRule:
    """Return the active rule; seeds version 1 (the default layout) when there is none."""
    active = await active_rule(session)
    if active is not None:
        return active
    anything = await session.scalar(select(func.count()).select_from(FilingRule))
    if anything:
        # rules exist but none is active (everything was deactivated): reactivate the newest
        newest = (
            await session.execute(select(FilingRule).order_by(FilingRule.version.desc()).limit(1))
        ).scalar_one()
        newest.active = True
        return newest
    rule = FilingRule(
        version=1, template=DEFAULT_TEMPLATE, fallbacks={}, active=True, note="default layout"
    )
    session.add(rule)
    await session.flush()
    return rule


async def active_rule(session: AsyncSession) -> FilingRule | None:
    """The currently active rule, if any."""
    return (
        await session.execute(select(FilingRule).where(FilingRule.active.is_(True)))
    ).scalar_one_or_none()


async def create_rule(
    session: AsyncSession,
    template: str,
    fallbacks: Mapping[str, Any],
    note: str,
    user_id: UUID | None,
) -> FilingRule:
    """Create a new, inactive rule version after validating the template."""
    parse_rule_template(template)
    await ensure_default_rule(session)
    version = (await session.scalar(select(func.max(FilingRule.version)))) or 0
    rule = FilingRule(
        version=version + 1,
        template=template,
        fallbacks=clean_fallbacks(fallbacks),
        active=False,
        created_by=user_id,
        note=note,
    )
    session.add(rule)
    await session.flush()
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="filing_rule.create",
        target_kind="filing_rule",
        target_id=rule.id,
        details={"version": rule.version, "template": template},
    )
    return rule


async def activate_rule(session: AsyncSession, rule_id: UUID, user_id: UUID | None) -> FilingRule:
    """Make ``rule_id`` the active rule (applies to assets filed from now on)."""
    rule = await session.get(FilingRule, rule_id)
    if rule is None:
        raise RuleError("filing rule not found")
    await session.execute(
        update(FilingRule)
        .where(FilingRule.active.is_(True), FilingRule.id != rule.id)
        .values(active=False)
    )
    rule.active = True
    await session.flush()
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="filing_rule.activate",
        target_kind="filing_rule",
        target_id=rule.id,
        details={"version": rule.version},
    )
    return rule


def context_for(
    asset: Asset, batch: Batch | None, taxonomy: Mapping[str, str] | None = None
) -> FilingContext:
    """Template values of an asset (``field.*`` stay empty until the taxonomy exists, M4).

    ``capture_date`` falls back to the batch folder's date hint when the files carry no
    recording date (common for exported or synthetic material).
    """
    capture = local_capture_date(asset.capture_at, asset.capture_tz)
    return FilingContext(
        media_type=asset.media_type,
        capture_date=capture or (batch.hint_date if batch else None),
        batch_label=batch.label if batch else None,
        batch_hint_date=batch.hint_date if batch else None,
        batch_hint_location=batch.hint_location if batch else None,
        batch_hint_project=batch.hint_project if batch else None,
        camera_model=asset.camera_model,
        taxonomy=taxonomy or {},
    )


def plan_for(
    root: Path | None,
    rule_template: str,
    fallbacks: Mapping[str, str],
    ctx: FilingContext,
    tail_length: int,
    budget: int = DEFAULT_PATH_BUDGET,
) -> DirectoryPlan:
    """Evaluate a stored template (falls back to the default layout if it cannot be parsed)."""
    try:
        template = parse_template(rule_template)
    except TemplateError:
        template = parse_template(DEFAULT_TEMPLATE)
    return plan_directory(root, template, ctx, fallbacks, tail_length, budget)


@dataclass(frozen=True)
class DryRunItem:
    """The computed location of one asset for a candidate rule."""

    asset_id: UUID
    filename: str
    created_at: datetime
    current_dir: str | None
    path: str
    warnings: tuple[str, ...]
    conflict: bool


async def dry_run(
    session: AsyncSession,
    root: Path | None,
    template: str,
    fallbacks: Mapping[str, Any],
    limit: int,
    budget: int = DEFAULT_PATH_BUDGET,
) -> list[DryRunItem]:
    """Compute where the newest ``limit`` assets would be filed with ``template``.

    A *conflict* is a result that needs attention: the path had to be shortened or still
    exceeds the budget, or an existing folder with a different spelling was adopted.
    """
    parsed = parse_rule_template(template)
    cleaned = clean_fallbacks(fallbacks)
    rows = (
        await session.execute(
            select(Asset, Batch, AssetFile.rel_path)
            .outerjoin(Batch, Batch.id == Asset.batch_id)
            .join(AssetFile, AssetFile.id == Asset.primary_file_id)
            .where(Asset.status.notin_(("duplicate", "trashed")))
            .order_by(Asset.id.desc())
            .limit(limit)
        )
    ).all()
    items: list[DryRunItem] = []
    for asset, batch, primary in rows:
        filename = primary.rsplit("/", 1)[-1]
        plan = plan_directory(
            root, parsed, context_for(asset, batch), cleaned, len(filename), budget
        )
        conflict = (
            bool(plan.adopted)
            or not plan.fits_budget
            or any("shortened" in w for w in plan.warnings)
        )
        items.append(
            DryRunItem(
                asset_id=asset.id,
                filename=filename,
                created_at=asset.created_at,
                current_dir=asset.content_rel_dir,
                path=plan.path,
                warnings=plan.warnings + plan.adopted,
                conflict=conflict,
            )
        )
    return items
