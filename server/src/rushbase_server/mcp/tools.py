"""MCP tool implementations on top of the existing services (no duplicated SQL).

Nothing here deletes or moves files: the registry in ``rushbase_core.mcp_tools`` has no such tool
and these handlers only touch the database (tags, notes, taxonomy, rules, needs, job queue).
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import Stage
from rushbase_core import mcp_tools as spec
from rushbase_server.api import catalog as catalog_api
from rushbase_server.api.deps import ROLE_RANK
from rushbase_server.mcp.output import (
    ToolFailure,
    ToolOutput,
    clamp_images,
    decode_cursor,
    encode_cursor,
    fit,
    load_image,
    sub_path_from_url,
)
from rushbase_server.models import (
    Asset,
    AudioEvent,
    Shot,
    TaxField,
    TranscriptSegment,
    User,
)
from rushbase_server.runtime import Runtime
from rushbase_server.search import hybrid
from rushbase_server.services import (
    ai_sidecar,
    catalog,
    coverage,
    editing,
    filing_rules,
    needs,
    reprocess,
    stats,
    taxonomy,
)

THUMB_EDGE = 256
KEYFRAME_EDGE = 512
M = TypeVar("M", bound=BaseModel)


@dataclass
class Ctx:
    """Everything a tool handler needs for one call."""

    runtime: Runtime
    db: AsyncSession
    user: User
    client_name: str

    def require_role(self, minimum: str) -> None:
        """Raise unless the consenting user has at least ``minimum`` rights."""
        if ROLE_RANK[self.user.role] < ROLE_RANK[minimum]:
            raise ToolFailure(
                f"this action needs the {minimum} role; the connected account is {self.user.role}"
            )


Handler = Callable[[Ctx, Any], Awaitable[ToolOutput]]


def _uuid(value: str, what: str = "id") -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ToolFailure(f"{what} is not a valid UUID") from exc


def _filters(filters: spec.MediaFilters, q: str | None = None) -> catalog.AssetFilters:
    base = needs.to_asset_filters(filters.model_dump(mode="json"))
    return catalog.AssetFilters(**{**base.__dict__, "q": q}) if q else base


def _brief(asset: Asset, filename: str | None) -> dict[str, Any]:
    """Compact asset description for lists."""
    data: dict[str, Any] = {
        "id": str(asset.id),
        "type": asset.media_type,
        "file": (filename or "").rsplit("/", 1)[-1],
        "title": asset.title,
        "summary": asset.summary_short,
        "captured": asset.capture_at.date().isoformat() if asset.capture_at else None,
        "duration_s": round(asset.duration_ms / 1000, 1) if asset.duration_ms else None,
        "size": f"{asset.width}x{asset.height}" if asset.width and asset.height else None,
        "camera": asset.camera_model,
        "status": asset.status,
        "review": asset.review_state,
        "rating": asset.rating,
    }
    return {k: v for k, v in data.items() if v is not None}


async def _hits(
    ctx: Ctx, result: hybrid.SearchResult, level: str, thumbnails: bool
) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]]]:
    items: list[dict[str, Any]] = []
    images: list[tuple[str, bytes]] = []
    root = ctx.runtime.binding.root
    for hit, row in await hybrid.hydrate(ctx.db, result.hits):
        item = {**_brief(row.asset, row.filename), "score": round(hit.score, 4)}
        shot = row.shot
        if shot is not None:
            item["shot"] = {
                "idx": shot.idx,
                "start_ms": shot.start_ms,
                "end_ms": shot.end_ms,
                "description": shot.description_short_en or shot.description_short_de,
            }
        items.append(item)
        if thumbnails and len(images) < spec.MAX_IMAGES:
            sub = None
            if shot is not None and shot.keyframes:
                frames = sorted(shot.keyframes, key=lambda k: k.get("position") != "middle")
                prefix = f".rushbase/derived/{row.asset.id}/"
                path = str(frames[0]["rel_path"])
                sub = path[len(prefix) :] if path.startswith(prefix) else None
            if sub is None:
                sub = sub_path_from_url(
                    catalog.derived_links(row.asset)["poster_url"], row.asset.id
                )
            data = await load_image(root, row.asset.id, sub, THUMB_EDGE)
            if data is not None:
                images.append((f"{len(items) - 1}: {item['file']}", data))
    return items, images


def _page(
    items: list[dict[str, Any]], total: int, offset: int, limit: int, extra: dict[str, Any]
) -> dict[str, Any]:
    payload: dict[str, Any] = {**extra, "total": total, "items": items}
    if offset + limit < total:
        payload["next_cursor"] = encode_cursor(offset + limit)
    return payload


async def search_media(ctx: Ctx, p: spec.SearchMedia) -> ToolOutput:
    """``search_media``."""
    offset = decode_cursor(p.cursor)
    result = await hybrid.search(
        ctx.db, ctx.runtime.encoder, p.q, p.level, _filters(p.filters), offset, p.limit
    )
    items, images = await _hits(ctx, result, p.level, p.include_thumbnails)
    extra: dict[str, Any] = {"level": p.level}
    if result.skipped:
        extra["semantic_channels_skipped"] = result.skipped
    payload = _page(items, result.candidates, offset, p.limit, extra)
    return ToolOutput(payload, clamp_images(images), list_key="items", offset=offset)


async def find_similar(ctx: Ctx, p: spec.FindSimilar) -> ToolOutput:
    """``find_similar``."""
    offset = decode_cursor(p.cursor)
    asset_id = _uuid(p.asset_id, "asset_id")
    shot_id = None
    if p.shot_idx is not None:
        shot_id = await ctx.db.scalar(
            select(Shot.id).where(Shot.asset_id == asset_id, Shot.idx == p.shot_idx)
        )
        if shot_id is None:
            raise ToolFailure("shot not found")
    result = await hybrid.similar(ctx.db, asset_id, shot_id, _filters(p.filters), offset, p.limit)
    if result is None:
        raise ToolFailure("no image vectors stored for this item yet (not analysed)")
    items, _ = await _hits(ctx, result, "shot" if shot_id else "asset", False)
    payload = _page(items, result.candidates, offset, p.limit, {})
    return ToolOutput(payload, list_key="items", offset=offset)


async def get_asset(ctx: Ctx, p: spec.GetAsset) -> ToolOutput:
    """``get_asset``."""
    asset_id = _uuid(p.asset_id, "asset_id")
    try:
        detail = await catalog_api.get_asset(asset_id, None, ctx.db)  # type: ignore[arg-type]
    except Exception as exc:
        raise ToolFailure("asset not found") from exc
    data = detail.model_dump(mode="json")
    for key in ("poster_url", "sprite_url", "sprite_tiles", "proxy_url", "waveform_url"):
        data.pop(key, None)
    for shot in data["shots"]:
        shot["keyframes"] = len(shot["keyframes"])
    data["history"] = data["history"][:8]
    data["files"] = [
        {"role": f["role"], "path": f["rel_path"], "size": f["size"], "os_paths": f["paths"]}
        for f in data["files"]
    ]
    notes = (await ctx.db.get_one(Asset, asset_id)).ai_details.get("notes", [])
    data["notes"] = notes[-10:]
    total = await ctx.db.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(TranscriptSegment.asset_id == asset_id)
    )
    segments = (
        await ctx.db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.asset_id == asset_id)
            .order_by(TranscriptSegment.idx)
            .offset(p.transcript_offset)
            .limit(p.transcript_limit)
        )
    ).scalars()
    data["transcript"] = {
        "total_segments": int(total or 0),
        "offset": p.transcript_offset,
        "segments": [
            {"i": s.idx, "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in segments
        ],
    }
    if p.transcript_offset + p.transcript_limit < int(total or 0):
        data["transcript"]["next_offset"] = p.transcript_offset + p.transcript_limit
    events = (
        await ctx.db.execute(
            select(AudioEvent)
            .where(AudioEvent.asset_id == asset_id)
            .order_by(AudioEvent.start_ms)
            .limit(30)
        )
    ).scalars()
    data["audio_events"] = [{"tag": e.tag, "start_ms": e.start_ms} for e in events]
    return ToolOutput(data, droppable=("history", "metadata", "audio_events", "files"))


async def get_keyframes(ctx: Ctx, p: spec.GetKeyframes) -> ToolOutput:
    """``get_keyframes``."""
    asset_id = _uuid(p.asset_id, "asset_id")
    stmt = select(Shot).where(Shot.asset_id == asset_id).order_by(Shot.idx)
    if p.shot_idx is not None:
        stmt = stmt.where(Shot.idx == p.shot_idx)
    shots = (await ctx.db.execute(stmt)).scalars().all()
    prefix = f".rushbase/derived/{asset_id}/"
    wanted: list[tuple[str, str]] = []
    for shot in shots:
        frames = sorted(shot.keyframes, key=lambda k: k.get("position") != "middle")
        chosen = frames if p.shot_idx is not None else frames[:1]
        for frame in chosen:
            path = str(frame["rel_path"])
            if path.startswith(prefix):
                wanted.append((f"shot {shot.idx} @ {frame['t_ms']} ms", path[len(prefix) :]))
    root = ctx.runtime.binding.root
    images: list[tuple[str, bytes]] = []
    for caption, sub in wanted[: p.limit]:
        data = await load_image(root, asset_id, sub, KEYFRAME_EDGE)
        if data is not None:
            images.append((caption, data))
    if not images:
        raise ToolFailure("no keyframes available (asset not processed or no library mounted)")
    return ToolOutput(
        {"frames": [c for c, _ in images], "available": len(wanted)}, clamp_images(images)
    )


async def list_facets(ctx: Ctx, p: spec.ListFacets) -> ToolOutput:
    """``list_facets``."""
    facets = await catalog.facets(ctx.db, _filters(p.filters, p.q))
    out = []
    for facet in facets:
        entry: dict[str, Any] = {
            "key": facet["key"],
            "label": facet["label_en"],
            "kind": facet["kind"],
        }
        if facet["kind"] == "enum":
            entry["values"] = [f"{v['value']} ({v['count']})" for v in facet["values"][:12]]
            entry["more"] = max(0, len(facet["values"]) - 12)
        else:
            entry.update(min=facet.get("min"), max=facet.get("max"))
        out.append(entry)
    return ToolOutput({"facets": out}, list_key="facets")


async def facet_counts(ctx: Ctx, p: spec.FacetCounts) -> ToolOutput:
    """``facet_counts``."""
    for facet in await catalog.facets(ctx.db, _filters(p.filters, p.q)):
        if facet["key"] == p.facet:
            if facet["kind"] != "enum":
                return ToolOutput(
                    {"facet": p.facet, "min": facet.get("min"), "max": facet.get("max")}
                )
            values = [{"value": v["value"], "count": v["count"]} for v in facet["values"]]
            return ToolOutput({"facet": p.facet, "values": values}, list_key="values")
    raise ToolFailure(f"unknown facet {p.facet!r}; call list_facets")


async def library_stats(ctx: Ctx, p: spec.LibraryStats) -> ToolOutput:
    """``library_stats``."""
    storage = await catalog.storage_stats(ctx.db)
    status_rows = await ctx.db.execute(select(Asset.status, func.count()).group_by(Asset.status))
    by_status = {str(state): int(count) for state, count in status_rows.all()}
    low, high, duration = (
        await ctx.db.execute(
            select(
                func.min(Asset.capture_at), func.max(Asset.capture_at), func.sum(Asset.duration_ms)
            ).where(Asset.status == "filed")
        )
    ).one()
    return ToolOutput(
        {
            "assets_by_status": by_status,
            "storage": storage["storage"],
            "duplicates": storage["duplicates"],
            "filed_footage_hours": round(int(duration or 0) / 3_600_000, 1),
            "capture_range": [
                low.date().isoformat() if low else None,
                high.date().isoformat() if high else None,
            ],
        }
    )


async def coverage_report(ctx: Ctx, p: spec.CoverageReport) -> ToolOutput:
    """``coverage_report``."""
    try:
        data = await coverage.coverage(
            ctx.db, p.rows, p.columns, _filters(p.filters), p.include_last_capture
        )
    except coverage.CoverageError as exc:
        raise ToolFailure(str(exc)) from exc
    return ToolOutput(data, list_key="cells")


async def gap_analysis(ctx: Ctx, p: spec.GapAnalysis) -> ToolOutput:
    """``gap_analysis``."""
    threshold = ctx.runtime.settings.needs_match_threshold
    out = []
    for item in p.items:
        result = await hybrid.search(
            ctx.db,
            ctx.runtime.encoder,
            item.description,
            "asset",
            _filters(item.filters),
            0,
            max(p.candidates, item.count),
        )
        hits, _ = await _hits(ctx, result, "asset", False)
        good = [h for h in hits if h["score"] >= threshold]
        state = "covered" if len(good) >= item.count else ("partial" if good else "missing")
        out.append(
            {
                "description": item.description,
                "wanted": item.count,
                "state": state,
                "matches": len(good),
                "candidates": [
                    {k: h[k] for k in ("id", "file", "summary", "score") if k in h}
                    for h in good[: p.candidates]
                ],
            }
        )
    return ToolOutput({"items": out}, list_key="items")


# --- needs -------------------------------------------------------------------------------


def _need_items(items: list[spec.GapItem]) -> list[tuple[str, dict[str, Any], int]]:
    return [(i.description, i.filters.model_dump(mode="json"), i.count) for i in items]


async def needs_create(ctx: Ctx, p: spec.NeedsCreate) -> ToolOutput:
    """``needs_create``."""
    ctx.require_role("editor")
    need = await needs.create_list(
        ctx.db, p.title, p.purpose, _need_items(p.items), "mcp", ctx.user.id
    )
    await ctx.db.commit()
    return ToolOutput(await needs.detail(ctx.db, need.id))


async def needs_list(ctx: Ctx, p: spec.NeedsList) -> ToolOutput:
    """``needs_list``."""
    offset = decode_cursor(p.cursor)
    rows, more = await needs.list_summaries(ctx.db, p.status, offset, p.limit)
    payload: dict[str, Any] = {"lists": rows}
    if more:
        payload["next_cursor"] = encode_cursor(offset + p.limit)
    return ToolOutput(payload, list_key="lists", offset=offset)


async def needs_get(ctx: Ctx, p: spec.NeedsGet) -> ToolOutput:
    """``needs_get``."""
    try:
        data = await needs.detail(ctx.db, _uuid(p.need_id, "need_id"))
    except needs.NeedsError as exc:
        raise ToolFailure(str(exc)) from exc
    images: list[tuple[str, bytes]] = []
    if p.include_thumbnails:
        root = ctx.runtime.binding.root
        for item in data["items"]:
            for candidate in item["candidates"]:
                if len(images) >= spec.MAX_IMAGES or candidate["status"] == "rejected":
                    continue
                asset = await ctx.db.get(Asset, UUID(candidate["asset_id"]))
                if asset is None:
                    continue
                sub = sub_path_from_url(catalog.derived_links(asset)["poster_url"], asset.id)
                image = await load_image(root, asset.id, sub, THUMB_EDGE)
                if image is not None:
                    images.append((f"{item['description'][:40]} -> {candidate['asset_id']}", image))
    return ToolOutput(data, images, list_key="items")


async def needs_update(ctx: Ctx, p: spec.NeedsUpdate) -> ToolOutput:
    """``needs_update``."""
    ctx.require_role("editor")
    try:
        need = await needs.load_list(ctx.db, _uuid(p.need_id, "need_id"))
        if p.title is not None:
            need.title = p.title.strip()
        if p.purpose is not None:
            need.purpose = p.purpose.strip()
        if p.add_items:
            await needs.add_items(ctx.db, need, _need_items(p.add_items))
        if p.item_id:
            item = await needs.load_item(ctx.db, _uuid(p.item_id, "item_id"))
            if item.list_id != need.id:
                raise ToolFailure("item belongs to another list")
            if p.item_status:
                item.status = p.item_status
            if p.item_count:
                item.count_wanted = p.item_count
    except needs.NeedsError as exc:
        raise ToolFailure(str(exc)) from exc
    await ctx.db.commit()
    return ToolOutput(await needs.detail(ctx.db, need.id), list_key="items")


async def needs_link_candidate(ctx: Ctx, p: spec.NeedsLinkCandidate) -> ToolOutput:
    """``needs_link_candidate``."""
    ctx.require_role("editor")
    try:
        item = await needs.load_item(ctx.db, _uuid(p.item_id, "item_id"))
        row = await needs.set_candidate(
            ctx.db, item, _uuid(p.asset_id, "asset_id"), p.shot_idx, p.status, "claude"
        )
    except needs.NeedsError as exc:
        raise ToolFailure(str(exc)) from exc
    await ctx.db.commit()
    return ToolOutput({"candidate": str(row.id), "status": row.status, "item_status": item.status})


async def needs_close(ctx: Ctx, p: spec.NeedsClose) -> ToolOutput:
    """``needs_close``."""
    ctx.require_role("editor")
    try:
        need = await needs.load_list(ctx.db, _uuid(p.need_id, "need_id"))
    except needs.NeedsError as exc:
        raise ToolFailure(str(exc)) from exc
    need.status = "open" if p.reopen else "closed"
    await ctx.db.commit()
    return ToolOutput({"id": str(need.id), "status": need.status})


# --- annotate ----------------------------------------------------------------------------


def _tag_refs(values: list[str]) -> list[editing.TagRef]:
    refs = []
    for raw in values:
        field, sep, value = raw.partition(":")
        if not sep or not field.strip() or not value.strip():
            raise ToolFailure(f"tag {raw!r} must look like field:value")
        refs.append(editing.TagRef(field.strip(), value.strip()))
    return refs


async def annotate_asset(ctx: Ctx, p: spec.AnnotateAsset) -> ToolOutput:
    """``annotate_asset``: source ``claude`` (never overrides or removes human tags)."""
    ctx.require_role("editor")
    asset = await ctx.db.get(Asset, _uuid(p.asset_id, "asset_id"))
    if asset is None:
        raise ToolFailure("asset not found")
    columns = {
        k: v
        for k, v in {
            "rating": p.rating,
            "locked": p.locked,
            "consent": p.consent,
            "consent_ref": p.consent_ref,
        }.items()
        if v is not None
    }
    edit = editing.Edit(
        columns=columns,
        add_tags=_tag_refs(p.add_tags),
        remove_tags=_tag_refs(p.remove_tags),
        source="claude",
    )
    try:
        details = await editing.edit_target(ctx.db, ctx.user.id, None, asset, None, edit)
    except editing.EditError as exc:
        raise ToolFailure(str(exc)) from exc
    if p.note:
        notes = list(asset.ai_details.get("notes", []))
        notes.append(
            {"at": ctx.runtime.clock.now().isoformat(), "by": ctx.client_name, "text": p.note}
        )
        asset.ai_details = {**asset.ai_details, "notes": notes[-50:]}
        details["note"] = "added"
    await ctx.db.commit()
    await ai_sidecar.refresh_for_runtime(ctx.runtime, ctx.db, asset.id)
    return ToolOutput({"asset_id": str(asset.id), "applied": details})


# --- taxonomy ----------------------------------------------------------------------------


def _taxonomy_error(exc: taxonomy.TaxonomyError) -> ToolFailure:
    return ToolFailure(str(exc))


async def taxonomy_list(ctx: Ctx, p: spec.TaxonomyList) -> ToolOutput:
    """``taxonomy_list``."""
    counts = await taxonomy.tag_counts(ctx.db)
    statuses = [p.status] if p.status else None
    fields = []
    for field, values in await taxonomy.fields_with_values(ctx.db, statuses):
        if p.field and field.key != p.field:
            continue
        fields.append(
            {
                "key": field.key,
                "label": field.label_en,
                "kind": field.kind,
                "description": field.description or None,
                "values": [
                    {
                        "id": str(v.id),
                        "value": v.value,
                        "status": v.status,
                        "assets": counts.get(v.id, 0),
                        **({"synonyms": list(v.synonyms)} if v.synonyms else {}),
                    }
                    for v in values
                ],
            }
        )
    payload = {"version": await taxonomy.current_version(ctx.db), "fields": fields}
    return ToolOutput(payload, list_key="fields")


def _actor(ctx: Ctx) -> taxonomy.Actor:
    return taxonomy.Actor(ctx.user.id, None)


async def taxonomy_propose_value(ctx: Ctx, p: spec.TaxonomyProposeValue) -> ToolOutput:
    """``taxonomy_propose_value``."""
    ctx.require_role("editor")
    field = (
        await ctx.db.execute(select(TaxField).where(TaxField.key == p.field))
    ).scalar_one_or_none()
    if field is None:
        raise ToolFailure(f"unknown field {p.field!r}; call taxonomy_list")
    try:
        row = await taxonomy.create_value(
            ctx.db,
            _actor(ctx),
            field.id,
            value=p.value,
            label_de=p.label_de,
            label_en=p.label_en,
            synonyms=p.synonyms,
            description=p.description,
            parent_id=None,
            approved=False,
            now=ctx.runtime.clock.now(),
            config=ctx.runtime.queue_config,
        )
    except taxonomy.TaxonomyError as exc:
        raise _taxonomy_error(exc) from exc
    await ctx.db.commit()
    return ToolOutput({"id": str(row.id), "value": row.value, "status": row.status})


async def taxonomy_approve(ctx: Ctx, p: spec.TaxonomyApprove) -> ToolOutput:
    """``taxonomy_approve``."""
    ctx.require_role("editor")
    try:
        row = await taxonomy.set_status(
            ctx.db, _actor(ctx), _uuid(p.value_id, "value_id"), "approved",
            ctx.runtime.clock.now(), ctx.runtime.queue_config,
        )  # fmt: skip
    except taxonomy.TaxonomyError as exc:
        raise _taxonomy_error(exc) from exc
    await ctx.db.commit()
    return ToolOutput({"id": str(row.id), "value": row.value, "status": row.status})


async def taxonomy_merge(ctx: Ctx, p: spec.TaxonomyMerge) -> ToolOutput:
    """``taxonomy_merge``."""
    ctx.require_role("admin")
    try:
        row = await taxonomy.merge_values(
            ctx.db, _actor(ctx), _uuid(p.value_id, "value_id"), _uuid(p.target_id, "target_id"),
            ctx.runtime.clock.now(), ctx.runtime.queue_config,
        )  # fmt: skip
    except taxonomy.TaxonomyError as exc:
        raise _taxonomy_error(exc) from exc
    await ctx.db.commit()
    return ToolOutput({"remaining": str(row.id), "value": row.value})


async def taxonomy_add_synonym(ctx: Ctx, p: spec.TaxonomyAddSynonym) -> ToolOutput:
    """``taxonomy_add_synonym``."""
    ctx.require_role("admin")
    try:
        row = await taxonomy.add_synonym(
            ctx.db, _actor(ctx), _uuid(p.value_id, "value_id"), p.synonym,
            ctx.runtime.clock.now(), ctx.runtime.queue_config,
        )  # fmt: skip
    except taxonomy.TaxonomyError as exc:
        raise _taxonomy_error(exc) from exc
    await ctx.db.commit()
    return ToolOutput({"id": str(row.id), "value": row.value, "synonyms": list(row.synonyms)})


# --- filing rules ------------------------------------------------------------------------


def _rule(rule: Any) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "version": rule.version,
        "template": rule.template,
        "fallbacks": rule.fallbacks,
        "active": rule.active,
        "note": rule.note,
    }


async def rules_get(ctx: Ctx, p: spec.RulesGet) -> ToolOutput:
    """``rules_get``."""
    rules = await filing_rules.list_rules(ctx.db)
    await ctx.db.commit()
    return ToolOutput(
        {
            "active": next((_rule(r) for r in rules if r.active), None),
            "recent_versions": [_rule(r) for r in rules[:5]],
        }
    )


async def rules_propose(ctx: Ctx, p: spec.RulesPropose) -> ToolOutput:
    """``rules_propose``: stores an inactive version and returns the dry run."""
    ctx.require_role("editor")
    try:
        results = await filing_rules.dry_run(
            ctx.db,
            ctx.runtime.binding.root,
            p.template,
            p.fallbacks,
            p.dry_run_limit,
            ctx.runtime.settings.filing_path_budget,
        )
        rule = await filing_rules.create_rule(ctx.db, p.template, p.fallbacks, p.note, ctx.user.id)
    except filing_rules.RuleError as exc:
        raise ToolFailure(str(exc)) from exc
    await ctx.db.commit()
    conflicts = [r for r in results if r.conflict]
    return ToolOutput(
        {
            "rule": _rule(rule),
            "dry_run": {
                "assets": len(results),
                "directories": len({r.path for r in results}),
                "conflicts": len(conflicts),
                "warnings": sorted({w for r in results for w in r.warnings})[:20],
                "sample": [
                    {"file": r.filename, "path": r.path, "conflict": r.conflict}
                    for r in (conflicts + results)[:15]
                ],
            },
            "next_step": "rules_activate with this rule id (applies to new material only)",
        }
    )


async def rules_activate(ctx: Ctx, p: spec.RulesActivate) -> ToolOutput:
    """``rules_activate``."""
    ctx.require_role("editor")
    try:
        rule = await filing_rules.activate_rule(ctx.db, _uuid(p.rule_id, "rule_id"), ctx.user.id)
    except filing_rules.RuleError as exc:
        raise ToolFailure(str(exc)) from exc
    await ctx.db.commit()
    return ToolOutput(_rule(rule))


# --- processing --------------------------------------------------------------------------


async def processing_status(ctx: Ctx, p: spec.ProcessingStatus) -> ToolOutput:
    """``processing_status``."""
    snapshot = await stats.system_status(
        ctx.db, ctx.runtime.clock.now(), ctx.runtime.settings.worker_online_seconds
    )
    data = asdict(snapshot)
    data["stages"] = {
        name: {"pending_jobs": s["pending_jobs"], "by_state": s["jobs"]}
        for name, s in data["stages"].items()
    }
    return ToolOutput(data)


async def reprocess_tool(ctx: Ctx, p: spec.Reprocess) -> ToolOutput:
    """``reprocess``: only creates jobs."""
    ctx.require_role("editor")
    if bool(p.asset_ids) == (p.filters is not None):
        raise ToolFailure("give either asset_ids or filters")
    ids = (
        [_uuid(a, "asset_id") for a in p.asset_ids]
        if p.asset_ids
        else await reprocess.select_assets(ctx.db, _filters(p.filters or spec.MediaFilters()))
    )
    queued, skipped = await reprocess.enqueue_stage(
        ctx.db,
        Stage(p.stage),
        ids,
        ctx.runtime.clock.now(),
        ctx.runtime.queue_config,
        "mcp",
        ctx.user.id,
    )
    await ctx.db.commit()
    return ToolOutput({"stage": p.stage, "queued": queued, "skipped": skipped})


HANDLERS: dict[str, Handler] = {
    "search_media": search_media,
    "get_asset": get_asset,
    "get_keyframes": get_keyframes,
    "find_similar": find_similar,
    "list_facets": list_facets,
    "facet_counts": facet_counts,
    "library_stats": library_stats,
    "coverage_report": coverage_report,
    "gap_analysis": gap_analysis,
    "needs_create": needs_create,
    "needs_list": needs_list,
    "needs_get": needs_get,
    "needs_update": needs_update,
    "needs_link_candidate": needs_link_candidate,
    "needs_close": needs_close,
    "annotate_asset": annotate_asset,
    "taxonomy_list": taxonomy_list,
    "taxonomy_propose_value": taxonomy_propose_value,
    "taxonomy_approve": taxonomy_approve,
    "taxonomy_merge": taxonomy_merge,
    "taxonomy_add_synonym": taxonomy_add_synonym,
    "rules_get": rules_get,
    "rules_propose": rules_propose,
    "rules_activate": rules_activate,
    "processing_status": processing_status,
    "reprocess": reprocess_tool,
}


def render(output: ToolOutput) -> str:
    """JSON text of an output within the size cap."""
    return fit(output.payload, output.list_key, output.offset, output.droppable)
