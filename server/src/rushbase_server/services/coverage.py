"""Coverage matrix: asset counts for two dimensions (technical columns or taxonomy fields)."""

from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from rushbase_server.models import Asset, AssetTag, TaxField, TaxValue
from rushbase_server.services.catalog import AssetFilters, apply_filters

TECHNICAL = (
    "media_type",
    "camera_model",
    "lens_model",
    "log_profile",
    "orientation",
    "codec",
    "shot_size",
    "camera_angle",
    "interior_exterior",
    "time_of_day",
)
MAX_CELLS = 400


class CoverageError(ValueError):
    """An unknown dimension was requested."""


async def _dimension(session: AsyncSession, key: str, stmt: Select[Any]) -> tuple[Any, Select[Any]]:
    """Value expression of a dimension and the statement joined as needed."""
    if key in TECHNICAL:
        return getattr(Asset, key), stmt
    field = (
        await session.execute(select(TaxField).where(TaxField.key == key))
    ).scalar_one_or_none()
    if field is None:
        raise CoverageError(
            f"unknown dimension {key!r}; use one of {', '.join(TECHNICAL)} or a taxonomy field key"
        )
    tag, value = aliased(AssetTag), aliased(TaxValue)
    joined = stmt.join(tag, and_(tag.asset_id == Asset.id, tag.field_id == field.id)).join(
        value, and_(value.id == tag.value_id, value.status != "deprecated")
    )
    return value.value, joined


async def coverage(
    session: AsyncSession,
    rows: str,
    columns: str,
    filters: AssetFilters,
    include_last_capture: bool,
) -> dict[str, Any]:
    """Counts of distinct assets per (row value, column value), largest cells first."""
    stmt: Select[Any] = select().select_from(Asset)
    row_expr, stmt = await _dimension(session, rows, stmt)
    col_expr, stmt = await _dimension(session, columns, stmt)
    columns_sel = [row_expr.label("r"), col_expr.label("c"), func.count(func.distinct(Asset.id))]
    if include_last_capture:
        columns_sel.append(func.max(Asset.capture_at))
    stmt = apply_filters(stmt.add_columns(*columns_sel), filters)
    stmt = stmt.where(row_expr.is_not(None), col_expr.is_not(None)).group_by(row_expr, col_expr)
    result = (
        await session.execute(
            stmt.order_by(func.count(func.distinct(Asset.id)).desc(), row_expr, col_expr).limit(
                MAX_CELLS + 1
            )
        )
    ).all()
    cells = []
    for record in result[:MAX_CELLS]:
        cell: dict[str, Any] = {"row": str(record[0]), "col": str(record[1]), "count": record[2]}
        if include_last_capture:
            cell["last_capture"] = record[3].date().isoformat() if record[3] else None
        cells.append(cell)
    return {
        "rows_dimension": rows,
        "columns_dimension": columns,
        "row_values": sorted({c["row"] for c in cells}),
        "column_values": sorted({c["col"] for c in cells}),
        "cells": cells,
        "truncated": len(result) > MAX_CELLS,
        "note": "Missing cells have zero assets.",
    }
