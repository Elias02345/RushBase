"""Dashboard data: storage growth, coverage heatmap, processing throughput (cached aggregates)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from rushbase_server.api.catalog import FiltersDep
from rushbase_server.api.deps import RuntimeDep, ViewerDep
from rushbase_server.services import coverage, dashboard

router = APIRouter(prefix="/api/v1/stats", tags=["dashboard"])


class TypeBucket(BaseModel):
    """Bytes and assets of one media type within a month."""

    bytes: int
    count: int


class GrowthMonth(BaseModel):
    """What was added to the library in one month (by ingest time)."""

    month: str
    bytes: int
    count: int
    by_media_type: dict[str, TypeBucket]


class ThroughputHour(BaseModel):
    """Successful stage results finished within one hour."""

    hour: str
    count: int
    by_stage: dict[str, int]


class ThroughputOut(BaseModel):
    """Hourly throughput of the last seven days."""

    since: str
    total: int
    per_hour_avg: float
    hours: list[ThroughputHour]


class DimensionOut(BaseModel):
    """A heatmap axis."""

    key: str
    label_de: str
    label_en: str
    kind: str


class HeatCell(BaseModel):
    """Assets in one (row, column) combination."""

    row: str
    col: str
    count: int


class HeatmapOut(BaseModel):
    """Coverage matrix with the weakest cells (zero cells included)."""

    rows_dimension: str
    columns_dimension: str
    row_values: list[str]
    column_values: list[str]
    cells: list[HeatCell]
    max: int
    truncated: bool
    weakest: list[HeatCell]


@router.get("/growth", response_model=list[GrowthMonth], summary="Storage added per month")
async def get_growth(_: ViewerDep, runtime: RuntimeDep) -> list[GrowthMonth]:
    """Bytes and assets added per month and media type (one grouped pass, cached)."""
    data = await runtime.cached("growth", dashboard.storage_growth)
    return [GrowthMonth(**month) for month in data]


@router.get("/throughput", response_model=ThroughputOut, summary="Jobs per hour, last 7 days")
async def get_throughput(_: ViewerDep, runtime: RuntimeDep) -> ThroughputOut:
    """Successful stage results per hour over the last seven days (cached)."""
    now = runtime.clock.now()
    data = await runtime.cached(
        ("throughput", now.replace(minute=0, second=0, microsecond=0)),
        lambda s: dashboard.throughput(s, now),
    )
    return ThroughputOut(**data)


@router.get("/dimensions", response_model=list[DimensionOut], summary="Heatmap axes")
async def get_dimensions(_: ViewerDep, runtime: RuntimeDep) -> list[DimensionOut]:
    """Technical columns and taxonomy fields that can be crossed in the coverage heatmap."""
    data = await runtime.cached("dimensions", dashboard.dimensions)
    return [DimensionOut(**vars(d)) for d in data]


@router.get("/coverage", response_model=HeatmapOut, summary="Coverage heatmap of two dimensions")
async def get_coverage(
    _: ViewerDep,
    runtime: RuntimeDep,
    filters: FiltersDep,
    rows: Annotated[str, Query(min_length=1, max_length=100)],
    columns: Annotated[str, Query(min_length=1, max_length=100)],
) -> HeatmapOut:
    """Asset counts per (row value, column value) and the weakest cells (cached)."""
    try:
        data = await runtime.cached(
            ("heatmap", rows, columns, filters),
            lambda s: dashboard.heatmap(s, rows, columns, filters),
        )
    except coverage.CoverageError as exc:
        raise HTTPException(422, str(exc)) from exc
    return HeatmapOut(**data)
