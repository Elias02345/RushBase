"""Hybrid search: full-text, e5 text vectors and SigLIP image vectors fused by RRF.

Filters (facets, media type ...) are applied in SQL inside every channel, before ranking. Vector
channels use the HNSW indexes with pgvector's iterative scan so selective filters still return
enough rows. A channel whose encoder or stored vectors are unavailable is skipped, never an error.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import BigInteger, Float, Select, Text, bindparam, cast, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import (
    Asset,
    AssetFile,
    Batch,
    EmbeddingImage,
    EmbeddingText,
    Shot,
    TranscriptSegment,
)
from rushbase_server.models.ai import Vector
from rushbase_server.search.encoder import EncoderInfo, QueryEncoder
from rushbase_server.services import catalog

logger = logging.getLogger(__name__)

RRF_K = 60
CANDIDATES = 200
MAX_DEPTH = 1000
Level = Literal["asset", "shot"]
Key = tuple[UUID, UUID | None]
"""``(asset_id, shot_id)``; ``shot_id`` is ``None`` at asset level."""


@dataclass
class Channel:
    """The ranked candidate keys of one retrieval channel."""

    name: str
    keys: list[Key]
    took_ms: float = 0.0


@dataclass
class Fused:
    """A fused hit: score and the 1-based rank in every channel that found it."""

    key: Key
    score: float
    ranks: dict[str, int] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Fused page plus bookkeeping for the response."""

    hits: list[Fused]
    candidates: int
    channels: dict[str, float]
    skipped: dict[str, str]


def rrf(channels: list[Channel]) -> list[Fused]:
    """Reciprocal Rank Fusion with ``k = 60`` (ties broken by key for stable pages)."""
    table: dict[Key, Fused] = {}
    for channel in channels:
        for rank, key in enumerate(channel.keys, start=1):
            hit = table.setdefault(key, Fused(key, 0.0))
            hit.score += 1.0 / (RRF_K + rank)
            hit.ranks[channel.name] = rank
    return sorted(table.values(), key=lambda h: (-h.score, str(h.key[0]), str(h.key[1])))


def _vector_param(vector: list[float]) -> Any:
    return cast(bindparam("qv", vector, type_=Vector()), Vector())


def _dedupe(rows: list[tuple[UUID, UUID | None]], limit: int) -> list[Key]:
    seen: set[Key] = set()
    keys: list[Key] = []
    for key in rows:
        if key not in seen:
            seen.add(key)
            keys.append(key)
            if len(keys) >= limit:
                break
    return keys


def _filtered[*Ts](stmt: Select[*Ts], filters: catalog.AssetFilters) -> Select[*Ts]:
    """Apply every filter except the free-text query (the channels handle ``q``)."""
    return catalog.apply_filters(stmt, replace(filters, q=None))


async def _tune_hnsw(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await session.execute(text("SET LOCAL hnsw.ef_search = 200"))


async def _shots_by_time(session: AsyncSession, asset_ids: set[UUID]) -> dict[UUID, list[Shot]]:
    shots: dict[UUID, list[Shot]] = defaultdict(list)
    if asset_ids:
        rows = await session.execute(
            select(Shot).where(Shot.asset_id.in_(asset_ids)).order_by(Shot.asset_id, Shot.idx)
        )
        for shot in rows.scalars():
            shots[shot.asset_id].append(shot)
    return shots


def _shot_at(shots: list[Shot], start_ms: int | None) -> UUID | None:
    if start_ms is None:
        return None
    for shot in shots:
        if shot.start_ms <= start_ms <= shot.end_ms:
            return shot.id
    return None


async def vector_channel(
    session: AsyncSession,
    name: str,
    table: type[EmbeddingImage] | type[EmbeddingText],
    model_id: str,
    revision: str | None,
    vector: list[float],
    level: Level,
    filters: catalog.AssetFilters,
    limit: int = CANDIDATES,
    exclude_asset: UUID | None = None,
    exclude_shot: UUID | None = None,
) -> Channel:
    """Nearest vectors of one space (cosine distance), de-duplicated to the requested level."""
    started = time.perf_counter()
    await _tune_hnsw(session)
    distance = table.embedding.op("<=>", return_type=Float)(_vector_param(vector))
    start: Any = EmbeddingText.start_ms if table is EmbeddingText else literal(None, BigInteger)
    stmt = (
        select(table.asset_id, table.shot_id, start.label("start_ms"), distance.label("d"))
        .join(Asset, Asset.id == table.asset_id)
        .where(table.model_id == model_id)
    )
    if revision:
        stmt = stmt.where(table.revision == revision)
    if exclude_asset is not None:
        stmt = stmt.where(table.asset_id != exclude_asset)
    if exclude_shot is not None:
        stmt = stmt.where((table.shot_id != exclude_shot) | (table.shot_id.is_(None)))
    stmt = _filtered(stmt, filters).order_by(distance).limit(limit * 4)
    rows: list[tuple[UUID, UUID | None, int | None]] = [
        (r[0], r[1], r[2]) for r in (await session.execute(stmt)).all()
    ]
    keys: list[Key] = []
    if level == "asset":
        keys = [(asset, None) for asset, _shot, _start in rows]
    else:
        timed = {a for a, shot, start_ms in rows if shot is None and start_ms is not None}
        mapping = await _shots_by_time(session, timed) if timed else {}
        for asset, shot, start_ms in rows:
            shot_id = shot if shot is not None else _shot_at(mapping.get(asset, []), start_ms)
            if shot_id is not None:
                keys.append((asset, shot_id))
    return Channel(name, _dedupe(keys, limit), (time.perf_counter() - started) * 1000)


async def fulltext_channels(
    session: AsyncSession, query: str, level: Level, filters: catalog.AssetFilters, limit: int
) -> list[Channel]:
    """PostgreSQL full-text channels: asset vectors, or shot descriptions plus transcripts."""
    terms = catalog.search_terms(query)
    usable = len(" ".join(terms)) >= catalog.MIN_FTS_LENGTH
    started = time.perf_counter()
    if level == "asset":
        keyed = replace(filters, q=query)
        stmt = catalog.apply_filters(select(Asset.id), keyed)
        if usable:
            rank = func.ts_rank_cd(Asset.search_tsv, catalog.tsquery(terms))
            stmt = stmt.order_by(rank.desc(), Asset.id)
        else:
            stmt = stmt.order_by(Asset.created_at.desc(), Asset.id)
        ids = (await session.execute(stmt.limit(limit))).scalars().all()
        return [Channel("fulltext", [(i, None) for i in ids], _ms(started))]
    if not usable:
        return []
    tsq = catalog.tsquery(terms)
    shot_stmt = _filtered(
        select(Shot.asset_id, Shot.id).join(Asset, Asset.id == Shot.asset_id), filters
    )
    shot_rows = await session.execute(
        shot_stmt.where(Shot.search_tsv.op("@@")(tsq))
        .order_by(func.ts_rank_cd(Shot.search_tsv, tsq).desc(), Shot.id)
        .limit(limit)
    )
    transcript_stmt = _filtered(
        select(TranscriptSegment.asset_id, Shot.id)
        .join(Asset, Asset.id == TranscriptSegment.asset_id)
        .join(
            Shot,
            (Shot.asset_id == TranscriptSegment.asset_id)
            & (TranscriptSegment.start_ms >= Shot.start_ms)
            & (TranscriptSegment.start_ms <= Shot.end_ms),
        ),
        filters,
    )
    transcript_rows = await session.execute(
        transcript_stmt.where(TranscriptSegment.tsv.op("@@")(tsq))
        .order_by(func.ts_rank_cd(TranscriptSegment.tsv, tsq).desc(), Shot.id)
        .limit(limit * 3)
    )
    return [
        Channel("fulltext", [(a, s) for a, s in shot_rows], _ms(started)),
        Channel("transcript", _dedupe([(a, s) for a, s in transcript_rows], limit), 0.0),
    ]


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


async def compatible_counts(
    session: AsyncSession, space: str, info: EncoderInfo
) -> tuple[int, int]:
    """``(compatible, mismatched)`` vector rows of a space for the server's encoder."""
    table = EmbeddingImage if space == "image" else EmbeddingText
    total = await session.scalar(select(func.count()).select_from(table)) or 0
    stmt = select(func.count()).select_from(table).where(table.model_id == info.model_id)
    if info.revision:
        stmt = stmt.where(table.revision == info.revision)
    compatible = await session.scalar(stmt) or 0
    return int(compatible), int(total) - int(compatible)


async def has_compatible(session: AsyncSession, space: str, info: EncoderInfo) -> bool:
    """Whether any stored vector of ``space`` matches the encoder (an index probe, no count)."""
    table = EmbeddingImage if space == "image" else EmbeddingText
    stmt = select(literal(1)).select_from(table).where(table.model_id == info.model_id)
    if info.revision:
        stmt = stmt.where(table.revision == info.revision)
    return await session.scalar(stmt.limit(1)) is not None


async def search(
    session: AsyncSession,
    encoder: QueryEncoder,
    query: str,
    level: Level,
    filters: catalog.AssetFilters,
    offset: int,
    limit: int,
) -> SearchResult:
    """Run all available channels and fuse them."""
    depth = min(MAX_DEPTH, max(CANDIDATES, offset + limit))
    channels = await fulltext_channels(session, query, level, filters, depth)
    skipped: dict[str, str] = {}
    for space, name, table, encode in (
        ("text", "text", EmbeddingText, encoder.encode_text_query),
        ("image", "image", EmbeddingImage, encoder.encode_image_query),
    ):
        info = encoder.info(space)
        if not info.available:
            skipped[name] = info.reason or "encoder unavailable"
            continue
        if not await has_compatible(session, space, info):
            skipped[name] = "no compatible vectors stored"
            continue
        started = time.perf_counter()
        vector = await asyncio.to_thread(encode, query)
        encoded_ms = _ms(started)
        channel = await vector_channel(
            session, name, table, info.model_id, info.revision, vector, level, filters, depth
        )
        channel.took_ms += encoded_ms
        channels.append(channel)
    fused = rrf(channels)
    timings = {c.name: round(c.took_ms, 1) for c in channels}
    logger.info(
        "search",
        extra={"level": level, "candidates": len(fused), "timings_ms": timings, "skipped": skipped},
    )
    return SearchResult(fused[offset : offset + limit], len(fused), timings, skipped)


async def similar(
    session: AsyncSession,
    asset_id: UUID,
    shot_id: UUID | None,
    filters: catalog.AssetFilters,
    offset: int,
    limit: int,
) -> SearchResult | None:
    """Nearest assets (or shots) by the stored image vectors of ``asset_id`` / ``shot_id``.

    Returns ``None`` when the source has no image vectors. No encoder is involved.
    """
    scope = EmbeddingImage.asset_id == asset_id
    if shot_id is not None:
        scope = scope & (EmbeddingImage.shot_id == shot_id)
    source = (
        await session.execute(
            select(
                EmbeddingImage.model_id,
                EmbeddingImage.revision,
                cast(func.avg(EmbeddingImage.embedding), Text),
            )
            .where(scope)
            .group_by(EmbeddingImage.model_id, EmbeddingImage.revision)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).first()
    if source is None:
        return None
    model_id, revision, literal_vector = source
    vector = [float(x) for x in json.loads(literal_vector)]
    depth = min(MAX_DEPTH, max(CANDIDATES, offset + limit + 1))
    level: Level = "shot" if shot_id is not None else "asset"
    channel = await vector_channel(
        session,
        "image",
        EmbeddingImage,
        model_id,
        revision,
        vector,
        level,
        filters,
        depth,
        exclude_asset=None if shot_id is not None else asset_id,
        exclude_shot=shot_id,
    )
    fused = rrf([channel])
    return SearchResult(
        fused[offset : offset + limit], len(fused), {"image": round(channel.took_ms, 1)}, {}
    )


@dataclass
class Hydrated:
    """Rows for rendering one hit."""

    asset: Asset
    filename: str | None
    batch_label: str | None
    shot: Shot | None


async def hydrate(session: AsyncSession, hits: list[Fused]) -> list[tuple[Fused, Hydrated]]:
    """Load assets (with primary file and batch) and shots of ``hits`` in two queries."""
    asset_ids = {h.key[0] for h in hits}
    shot_ids = {h.key[1] for h in hits if h.key[1] is not None}
    assets = {}
    if asset_ids:
        rows = await session.execute(
            select(Asset, AssetFile.rel_path, Batch.label)
            .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
            .outerjoin(Batch, Batch.id == Asset.batch_id)
            .where(Asset.id.in_(asset_ids))
        )
        assets = {a.id: (a, p, label) for a, p, label in rows}
    shots = {}
    if shot_ids:
        shots = {
            s.id: s
            for s in (await session.execute(select(Shot).where(Shot.id.in_(shot_ids)))).scalars()
        }
    result: list[tuple[Fused, Hydrated]] = []
    for hit in hits:
        if hit.key[0] not in assets:
            continue
        asset, path, label = assets[hit.key[0]]
        shot = shots.get(hit.key[1]) if hit.key[1] else None
        result.append((hit, Hydrated(asset, path, label, shot)))
    return result
