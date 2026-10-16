"""Read side of the catalog: asset listing with keyset pagination, details, facets, statistics.

All queries run on PostgreSQL alone (ADR-0012); nothing here needs a worker or the share.
"""

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Numeric, Select, String, cast, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, AssetFile, AuditLog, Batch
from rushbase_server.services.derived import derived_url

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_SPRITE = re.compile(r"^sprite(?:_(\d+))?\.[A-Za-z0-9]+$")
POSTER_KINDS = ("poster", "thumbnail", "photo_preview")
SEARCH_CONFIG = "rushbase_simple"
MIN_FTS_LENGTH = 3
MAX_QUERY_TERMS = 12
_TERM_SPLIT = re.compile(r"[\W_]+")


class InvalidQueryError(ValueError):
    """A list parameter (cursor, sort) is malformed."""


@dataclass(frozen=True)
class AssetFilters:
    """Filters of the asset list (all optional)."""

    media_type: str | None = None
    status: str | None = None
    batch_id: UUID | None = None
    camera_model: str | None = None
    lens_model: str | None = None
    log_profile: str | None = None
    orientation: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None


def search_terms(q: str) -> list[str]:
    """Words of a user query: split at every non-word character, so no tsquery syntax survives."""
    return [t for t in _TERM_SPLIT.split(q) if t][:MAX_QUERY_TERMS]


def _tsquery(terms: list[str]) -> ColumnElement[Any]:
    """AND of all terms; the last one matches as a prefix (search-as-you-type)."""
    text = " & ".join(terms) + ":*"
    return func.to_tsquery(cast(literal(SEARCH_CONFIG), REGCONFIG), text)


def _fts_terms(filters: AssetFilters) -> list[str] | None:
    """Terms when the query is long enough for full-text search, else ``None`` (ILIKE fallback)."""
    if not filters.q:
        return None
    terms = search_terms(filters.q)
    return terms if len(" ".join(terms)) >= MIN_FTS_LENGTH else None


def _filename_expr() -> ColumnElement[str]:
    return func.lower(func.regexp_replace(AssetFile.rel_path, "^.*/", ""))


@dataclass(frozen=True)
class SortKey:
    """A sort option: SQL expression plus (de)serialisation of cursor values."""

    expression: Callable[[], ColumnElement[Any]]
    encode: Callable[[Any], Any]
    decode: Callable[[Any], Any]


def _identity(value: Any) -> Any:
    return value


SORTS: dict[str, SortKey] = {
    "date": SortKey(
        lambda: func.coalesce(Asset.capture_at, Asset.created_at),
        lambda v: v.isoformat(),
        datetime.fromisoformat,
    ),
    "created_at": SortKey(
        lambda: Asset.created_at.expression, lambda v: v.isoformat(), datetime.fromisoformat
    ),
    "size": SortKey(lambda: Asset.size_total.expression, _identity, int),
    "duration": SortKey(lambda: func.coalesce(Asset.duration_ms, -1), _identity, int),
    "filename": SortKey(_filename_expr, _identity, str),
}


def _relevance_key(terms: list[str]) -> SortKey:
    """Rank by ``ts_rank_cd`` (weights A > B > C > D), rounded so the cursor compares exactly."""
    return SortKey(
        lambda: func.round(cast(func.ts_rank_cd(Asset.search_tsv, _tsquery(terms)), Numeric), 4),
        str,
        _decode_rank,
    )


def _decode_rank(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid rank") from exc


def parse_sort(sort: str | None) -> tuple[str, bool]:
    """``"-date"`` -> ``("date", True)``; the default is newest first."""
    text = sort or "-date"
    descending = text.startswith("-")
    name = text.lstrip("+-")
    if name not in SORTS:
        raise InvalidQueryError(f"unknown sort {text!r}; use one of {', '.join(SORTS)}")
    return name, descending


def encode_cursor(value: Any, asset_id: UUID) -> str:
    """Opaque cursor for ``(sort value, id)``."""
    raw = json.dumps([value, str(asset_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, UUID]:
    """Inverse of :func:`encode_cursor`; raises :class:`InvalidQueryError`."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value, asset_id = json.loads(base64.urlsafe_b64decode(padded))
        return value, UUID(asset_id)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidQueryError("invalid cursor") from exc


def _apply_filters[*Ts](stmt: Select[*Ts], f: AssetFilters) -> Select[*Ts]:
    if f.status:
        stmt = stmt.where(Asset.status == f.status)
    else:
        stmt = stmt.where(Asset.status != "trashed")
    for column, value in (
        (Asset.media_type, f.media_type),
        (Asset.batch_id, f.batch_id),
        (Asset.camera_model, f.camera_model),
        (Asset.lens_model, f.lens_model),
        (Asset.log_profile, f.log_profile),
        (Asset.orientation, f.orientation),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    when = func.coalesce(Asset.capture_at, Asset.created_at)
    if f.date_from:
        stmt = stmt.where(when >= datetime.combine(f.date_from, time.min, UTC))
    if f.date_to:
        stmt = stmt.where(when <= datetime.combine(f.date_to, time.max, UTC))
    if f.q:
        if (terms := _fts_terms(f)) is not None:
            return stmt.where(Asset.search_tsv.op("@@")(_tsquery(terms)))
        match = (
            select(literal(1))
            .select_from(AssetFile)
            .correlate(Asset)
            .where(
                AssetFile.asset_id == Asset.id, AssetFile.rel_path.icontains(f.q, autoescape=True)
            )
        )
        stmt = stmt.where(match.exists())
    return stmt


@dataclass(frozen=True)
class AssetPage:
    """One page of rows ``(asset, primary rel_path, batch label)``."""

    rows: list[tuple[Asset, str | None, str | None]]
    next_cursor: str | None
    total: int | None


async def list_assets(
    session: AsyncSession,
    filters: AssetFilters,
    sort: str | None,
    cursor: str | None,
    limit: int,
) -> AssetPage:
    """Return a keyset-paginated page; ``total`` is only computed for the first page.

    Without ``sort`` and with a full-text query the order is relevance (rank rounded to four
    decimals, then id, both descending) so the keyset cursor stays exact; otherwise ``sort``
    (default ``-date``) applies.
    """
    terms = _fts_terms(filters)
    if sort is None and terms is not None:
        key, descending = _relevance_key(terms), True
    else:
        name, descending = parse_sort(sort)
        key = SORTS[name]
    expression = key.expression().label("sort_key")
    base = (
        select(Asset, AssetFile.rel_path, Batch.label, expression)
        .outerjoin(AssetFile, AssetFile.id == Asset.primary_file_id)
        .outerjoin(Batch, Batch.id == Asset.batch_id)
    )
    stmt = _apply_filters(base, filters)
    if cursor:
        value, asset_id = decode_cursor(cursor)
        try:
            pair = tuple_(key.expression(), Asset.id)
            marker = tuple_(key.decode(value), asset_id)
        except (ValueError, TypeError) as exc:
            raise InvalidQueryError("invalid cursor") from exc
        stmt = stmt.where(pair < marker if descending else pair > marker)
    order = (expression.desc(), Asset.id.desc()) if descending else (expression, Asset.id)
    result = (await session.execute(stmt.order_by(*order).limit(limit + 1))).all()
    page = result[:limit]
    next_cursor = None
    if len(result) > limit:
        last = page[-1]
        next_cursor = encode_cursor(key.encode(last[3]), last[0].id)
    total = None
    if not cursor:
        counted = _apply_filters(select(func.count()).select_from(Asset), filters)
        total = int(await session.scalar(counted) or 0)
    return AssetPage([(r[0], r[1], r[2]) for r in page], next_cursor, total)


def derived_links(asset: Asset) -> dict[str, Any]:
    """URLs of poster, sprite (with tile count), proxy and waveform from the derived files."""
    links: dict[str, Any] = {
        "poster_url": None,
        "sprite_url": None,
        "sprite_tiles": None,
        "proxy_url": None,
        "waveform_url": None,
    }
    entries = asset.derived.get("files", [])
    for kind in POSTER_KINDS:
        for item in entries:
            if item.get("kind") == kind and links["poster_url"] is None:
                links["poster_url"] = derived_url(asset.id, item["rel_path"])
    for item in entries:
        name = item["rel_path"].rsplit("/", 1)[-1]
        if item.get("kind") == "proxy" and links["proxy_url"] is None:
            links["proxy_url"] = derived_url(asset.id, item["rel_path"])
        elif item.get("kind") == "waveform" and links["waveform_url"] is None:
            links["waveform_url"] = derived_url(asset.id, item["rel_path"])
        elif item.get("kind") == "sprite" and not links["sprite_url"]:
            links["sprite_url"] = derived_url(asset.id, item["rel_path"])
            links["sprite_tiles"] = item.get("tiles")
        elif item.get("kind") == "other" and (m := _SPRITE.match(name)) and not links["sprite_url"]:
            # Legacy naming convention from before the ``sprite`` kind existed.
            links["sprite_url"] = derived_url(asset.id, item["rel_path"])
            links["sprite_tiles"] = int(m[1]) if m[1] else None
    return links


async def history(session: AsyncSession, asset_id: UUID) -> list[AuditLog]:
    """Audit entries about an asset, oldest first."""
    rows = await session.execute(
        select(AuditLog)
        .where(AuditLog.target_kind == "asset", AuditLog.target_id == str(asset_id))
        .order_by(AuditLog.at, AuditLog.id)
    )
    return list(rows.scalars())


def platform_paths(roots: Mapping[str, Any], rel_path: str) -> dict[str, str]:
    """Absolute paths of a library-relative file per configured operating system."""
    paths: dict[str, str] = {}
    for platform, root in roots.items():
        if platform not in ("windows", "macos", "linux") or not root:
            continue
        base = str(root).rstrip("/\\")
        if platform == "windows":
            paths[platform] = base + "\\" + rel_path.replace("/", "\\")
        else:
            paths[platform] = f"{base}/{rel_path}"
    return paths


# --- facets and statistics -----------------------------------------------------------------

FACET_LABELS = {
    "media_type": ("Medientyp", "Media type"),
    "camera_model": ("Kamera", "Camera"),
    "lens_model": ("Objektiv", "Lens"),
    "log_profile": ("Log-Profil", "Log profile"),
    "orientation": ("Ausrichtung", "Orientation"),
    "codec": ("Codec", "Codec"),
}


async def facets(
    session: AsyncSession, filters: AssetFilters | None = None
) -> list[dict[str, Any]]:
    """Enum facets with counts plus duration (range) and capture date (date) facets.

    All active filters and the search query apply. An enum facet ignores its own filter so its
    other values stay selectable (``camera_model=A`` still lists camera B with its count).
    """
    active = filters or AssetFilters()
    result: list[dict[str, Any]] = []
    for key, (de, en) in FACET_LABELS.items():
        column = getattr(Asset, key)
        own = replace(active, **{key: None}) if hasattr(active, key) else active
        stmt = _apply_filters(select(column, func.count()).select_from(Asset), own)
        rows = await session.execute(
            stmt.where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column)
            .limit(100)
        )
        values = [{"value": str(v), "count": c} for v, c in rows]
        if values:
            result.append(
                {"key": key, "label_de": de, "label_en": en, "kind": "enum", "values": values}
            )
    low, high = (
        await session.execute(
            _apply_filters(select(func.min(Asset.duration_ms), func.max(Asset.duration_ms)), active)
        )
    ).one()
    if high is not None:
        result.append(
            {
                "key": "duration_ms",
                "label_de": "Dauer",
                "label_en": "Duration",
                "kind": "range",
                "min": low,
                "max": high,
            }
        )
    captures = select(func.min(Asset.capture_at), func.max(Asset.capture_at))
    first, last = (await session.execute(_apply_filters(captures, active))).one()
    if first is not None:
        result.append(
            {
                "key": "capture_at",
                "label_de": "Aufnahmedatum",
                "label_en": "Capture date",
                "kind": "date",
                "min": first.isoformat(),
                "max": last.isoformat() if last else None,
            }
        )
    return result


async def storage_stats(session: AsyncSession) -> dict[str, Any]:
    """Storage totals by media type and capture year, plus duplicate waste."""
    counted = Asset.status.notin_(("duplicate", "trashed"))
    total = await session.scalar(
        select(func.coalesce(func.sum(Asset.size_total), 0)).where(counted)
    )
    by_type = await session.execute(
        select(Asset.media_type, func.sum(Asset.size_total), func.count())
        .where(counted)
        .group_by(Asset.media_type)
        .order_by(Asset.media_type)
    )
    year = func.extract("year", func.coalesce(Asset.capture_at, Asset.created_at)).cast(String)
    by_year = await session.execute(
        select(year, func.sum(Asset.size_total), func.count())
        .where(counted)
        .group_by(year)
        .order_by(year)
    )
    dup_count, wasted = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Asset.size_total), 0)).where(
                Asset.status == "duplicate"
            )
        )
    ).one()
    return {
        "storage": {
            "total_bytes": int(total or 0),
            "by_media_type": [{"key": k, "bytes": int(b), "count": c} for k, b, c in by_type],
            "by_year": [
                {"year": int(float(y)), "bytes": int(b), "count": c} for y, b, c in by_year
            ],
        },
        "duplicates": {"count": int(dup_count), "wasted_bytes": int(wasted)},
    }
