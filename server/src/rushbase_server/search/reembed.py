"""Admin action "re-embed": re-index assets whose stored vectors do not match the encoder."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_core import Stage, uuid7
from rushbase_server.models import Asset, Batch, EmbeddingImage, EmbeddingText, Job
from rushbase_server.search.encoder import QueryEncoder
from rushbase_server.services import audit, queue
from rushbase_server.services.queue import INACTIVE_ASSET_STATUSES, OPEN_STATES


async def mismatched_assets(session: AsyncSession, encoder: QueryEncoder) -> set[UUID]:
    """Assets with at least one vector of another model id / revision than the encoder's."""
    found: set[UUID] = set()
    for space, table in (("image", EmbeddingImage), ("text", EmbeddingText)):
        info = encoder.info(space)
        if not info.model_id:
            continue
        stmt = select(table.asset_id).where(table.model_id != info.model_id)
        if info.revision:
            stmt = select(table.asset_id).where(
                (table.model_id != info.model_id) | (table.revision != info.revision)
            )
        found |= set((await session.execute(stmt.distinct())).scalars())
    return found


async def enqueue(
    session: AsyncSession,
    encoder: QueryEncoder,
    now: datetime,
    config: queue.QueueConfig,
    user_id: UUID | None,
) -> int:
    """Queue ``embed`` (and ``classify`` behind it) for the mismatched assets."""
    candidates = await mismatched_assets(session, encoder)
    if not candidates:
        return 0
    open_embed = set(
        (
            await session.execute(
                select(Job.asset_id).where(
                    Job.stage == Stage.EMBED.value, Job.state.in_(OPEN_STATES)
                )
            )
        ).scalars()
    )
    rows = (
        await session.execute(
            select(Asset, Batch)
            .outerjoin(Batch, Batch.id == Asset.batch_id)
            .where(
                Asset.id.in_(candidates - open_embed),
                Asset.status.notin_(INACTIVE_ASSET_STATUSES),
                Asset.locked.is_(False),
            )
        )
    ).all()
    for asset, batch in rows:
        embed_id = uuid7()
        common = {
            "asset_id": asset.id,
            "priority": batch.priority if batch else 0,
            "enqueued_at": batch.first_seen_at if batch else now,
            "max_attempts": config.max_attempts,
            "requirements": {},
        }
        session.add(
            Job(id=embed_id, stage=Stage.EMBED.value, state="queued", depends_on=[], **common)
        )
        session.add(
            Job(
                id=uuid7(),
                stage=Stage.CLASSIFY.value,
                state="blocked",
                depends_on=[embed_id],
                **common,
            )
        )
    audit.record(
        session,
        actor_kind="user" if user_id else "system",
        actor_id=user_id,
        action="search.reembed",
        details={"assets": len(rows)},
    )
    return len(rows)
