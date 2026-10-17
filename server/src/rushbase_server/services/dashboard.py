"""Dashboard aggregates: storage growth, coverage heatmap with gaps, processing throughput.

Every function is one grouped pass over the table it reads, so the cost does not depend on how
the result is drawn; callers serve them from the TTL cache.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, StageResult, TaxField, TaxValue
from rushbase_server.services import coverage
from rushbase_server.services.catalog import FACET_LABELS, AssetFilters

THROUGHPUT_DAYS = 7
WEAKEST_LIMIT = 10
MAX_AXIS_VALUES = 60
# ``codec`` has no library filter, so its heatmap cells could not link to a filtered library.
HEATMAP_TECHNICAL = tuple(key for key in coverage.TECHNICAL if key != "codec")


async def storage_growth(session: AsyncSession) -> list[dict[str, Any]]:
    """Bytes and asset count added per month (ingest time) and media type, oldest month first."""
    month = func.date_trunc("month", Asset.created_at)
    rows = await session.execute(
        select(month, Asset.media_type, func.coalesce(func.sum(Asset.size_total), 0), func.count())
        .where(Asset.status != "trashed")
        .group_by(month, Asset.media_type)
        .order_by(month, Asset.media_type)
    )
    months: dict[str, dict[str, Any]] = {}
    for start, media_type, size, count in rows:
        key = start.strftime("%Y-%m")
        entry = months.setdefault(key, {"month": key, "bytes": 0, "count": 0, "by_media_type": {}})
        entry["bytes"] += int(size)
        entry["count"] += int(count)
        entry["by_media_type"][media_type] = {"bytes": int(size), "count": int(count)}
    return list(months.values())


async def throughput(session: AsyncSession, now: datetime) -> dict[str, Any]:
    """Successful stage results per hour over the last seven days (``by_stage`` per bucket)."""
    since = (now - timedelta(days=THROUGHPUT_DAYS)).replace(minute=0, second=0, microsecond=0)
    hour = func.date_trunc("hour", StageResult.finished_at)
    rows = await session.execute(
        select(hour, StageResult.stage, func.count())
        .where(StageResult.ok.is_(True), StageResult.finished_at >= since)
        .group_by(hour, StageResult.stage)
        .order_by(hour)
    )
    buckets: dict[datetime, dict[str, int]] = {}
    for start, stage, count in rows:
        buckets.setdefault(start, {})[stage] = int(count)
    hours: list[dict[str, Any]] = []
    cursor = since
    end = now.replace(minute=0, second=0, microsecond=0)
    while cursor <= end:
        by_stage = buckets.get(cursor, {})
        hours.append(
            {"hour": cursor.isoformat(), "count": sum(by_stage.values()), "by_stage": by_stage}
        )
        cursor += timedelta(hours=1)
    total = sum(h["count"] for h in hours)
    return {
        "since": since.isoformat(),
        "total": total,
        "per_hour_avg": total / max(1, len(hours)),
        "hours": hours,
    }


@dataclass(frozen=True)
class Dimension:
    """A heatmap axis: a technical column or a taxonomy field."""

    key: str
    label_de: str
    label_en: str
    kind: str


async def dimensions(session: AsyncSession) -> list[Dimension]:
    """The axes the heatmap offers (all of them can be turned into a library filter)."""
    result = [
        Dimension(key, *FACET_LABELS.get(key, (key, key)), "technical") for key in HEATMAP_TECHNICAL
    ]
    fields = (
        await session.execute(select(TaxField).order_by(TaxField.sort, TaxField.key))
    ).scalars()
    result += [Dimension(f.key, f.label_de, f.label_en, "taxonomy") for f in fields]
    return result


async def _observed_values(session: AsyncSession, key: str) -> list[str]:
    column = getattr(Asset, key)
    rows = await session.execute(
        select(column).where(column.is_not(None), Asset.status != "trashed").distinct()
    )
    return sorted(str(v) for v in rows.scalars())


async def _approved_values(session: AsyncSession, key: str) -> list[str]:
    rows = await session.execute(
        select(TaxValue.value)
        .join(TaxField, TaxField.id == TaxValue.field_id)
        .where(TaxField.key == key, TaxValue.status == "approved")
    )
    return sorted(rows.scalars())


async def heatmap(
    session: AsyncSession, rows: str, columns: str, filters: AssetFilters
) -> dict[str, Any]:
    """Coverage of ``rows`` x ``columns`` plus the weakest cells, zero cells included.

    Axes list every value that exists (observed for technical columns, approved for taxonomy
    fields), so a combination nothing was filed under shows as a gap.
    Raises :class:`coverage.CoverageError` for unknown dimensions.
    """
    data = await coverage.coverage(session, rows, columns, filters, False)
    axes: dict[str, list[str]] = {}
    for key, observed in ((rows, data["row_values"]), (columns, data["column_values"])):
        values = set(observed)
        if key in HEATMAP_TECHNICAL:
            values |= set(await _observed_values(session, key))
        else:
            values |= set(await _approved_values(session, key))
        axes[key] = sorted(values)[:MAX_AXIS_VALUES]
    counts = {(c["row"], c["col"]): c["count"] for c in data["cells"]}
    row_values, col_values = axes[rows], axes[columns]
    maximum = max(counts.values(), default=0)
    complete = not data["truncated"]
    candidates = sorted(
        ((counts.get((r, c), 0), r, c) for r in row_values for c in col_values),
        key=lambda cell: (cell[0], cell[1], cell[2]),
    )
    return {
        **data,
        "row_values": row_values,
        "column_values": col_values,
        "max": maximum,
        "weakest": (
            [{"row": r, "col": c, "count": n} for n, r, c in candidates[:WEAKEST_LIMIT]]
            if complete
            else []
        ),
    }
