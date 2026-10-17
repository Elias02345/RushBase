"""Read side of the catalog: asset listing with keyset pagination, details, facets, statistics.

All queries run on PostgreSQL alone (ADR-0012); nothing here needs a worker or the share.
"""

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Integer, Numeric, Select, cast, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import (
    Asset,
    AssetFile,
    AssetTag,
    AuditLog,
    Batch,
    CollectionAsset,
    TaxField,
    TaxValue,
)
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
    shot_size: str | None = None
    camera_angle: str | None = None
    interior_exterior: str | None = None
    time_of_day: str | None = None
    person_identifiable: str | None = None
    review_state: str | None = None
    consent: str | None = None
    review_reason: str | None = None
    collection_id: UUID | None = None
    tags: tuple[str, ...] = ()
    """Taxonomy filters ``field:value``; values of one field are alternatives, fields combine."""


def search_terms(q: str) -> list[str]:
    """Words of a user query: split at every non-word character, so no tsquery syntax survives."""
    return [t for t in _TERM_SPLIT.split(q) if t][:MAX_QUERY_TERMS]


def tsquery(terms: list[str]) -> ColumnElement[Any]:
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
        lambda: func.round(cast(func.ts_rank_cd(Asset.search_tsv, tsquery(terms)), Numeric), 4),
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


def _tag_groups(tags: Sequence[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for item in tags:
        field_key, _, value = item.partition(":")
        if field_key and value:
            groups.setdefault(field_key, []).append(value.lower())
    return groups


def _has_tag(field_key: str, values: list[str]) -> ColumnElement[bool]:
    """EXISTS an approved tag of ``field_key`` with one of ``values`` on the asset."""
    return (
        select(literal(1))
        .select_from(AssetTag)
        .join(TaxField, TaxField.id == AssetTag.field_id)
        .join(TaxValue, TaxValue.id == AssetTag.value_id)
        .where(
            AssetTag.asset_id == Asset.id,
            TaxField.key == field_key,
            TaxValue.status == "approved",
            func.lower(TaxValue.value).in_(values),
        )
        .correlate(Asset)
        .exists()
    )


def apply_filters[*Ts](stmt: Select[*Ts], f: AssetFilters) -> Select[*Ts]:
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
        (Asset.shot_size, f.shot_size),
        (Asset.camera_angle, f.camera_angle),
        (Asset.interior_exterior, f.interior_exterior),
        (Asset.time_of_day, f.time_of_day),
        (Asset.person_identifiable, f.person_identifiable),
        (Asset.review_state, f.review_state),
        (Asset.consent, f.consent),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    when = func.coalesce(Asset.capture_at, Asset.created_at)
    if f.date_from:
        stmt = stmt.where(when >= datetime.combine(f.date_from, time.min, UTC))
    if f.date_to:
        stmt = stmt.where(when <= datetime.combine(f.date_to, time.max, UTC))
    if f.review_reason:
        stmt = stmt.where(Asset.review_reasons.contains([f.review_reason]))
    if f.collection_id:
        stmt = stmt.where(
            select(literal(1))
            .select_from(CollectionAsset)
            .where(
                CollectionAsset.asset_id == Asset.id,
                CollectionAsset.collection_id == f.collection_id,
            )
            .correlate(Asset)
            .exists()
        )
    for field_key, values in _tag_groups(f.tags).items():
        stmt = stmt.where(_has_tag(field_key, values))
    if f.q:
        if (terms := _fts_terms(f)) is not None:
            return stmt.where(Asset.search_tsv.op("@@")(tsquery(terms)))
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
    stmt = apply_filters(base, filters)
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
        counted = apply_filters(select(func.count()).select_from(Asset), filters)
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
    "shot_size": ("Einstellungsgröße", "Shot size"),
    "camera_angle": ("Kamerawinkel", "Camera angle"),
    "interior_exterior": ("Innen/Außen", "Interior/exterior"),
    "time_of_day": ("Tageszeit", "Time of day"),
    "person_identifiable": ("Person erkennbar", "Person identifiable"),
    "review_state": ("Prüfstatus", "Review state"),
    "consent": ("Einwilligung", "Consent"),
}


FACET_VALUE_LIMIT = 100


def cast_kwargs(key: str) -> dict[str, Any]:
    """``{key: None}`` as keyword arguments that clear one filter."""
    return {key: None}


def _own_filter_set(active: AssetFilters, key: str) -> bool:
    return getattr(active, key, None) is not None


async def _enum_counts(
    session: AsyncSession, keys: Sequence[str], filters: AssetFilters
) -> dict[str, list[tuple[str, int]]]:
    """Value counts of several enum columns in a single pass (``GROUPING SETS``)."""
    if not keys:
        return {}
    columns = [getattr(Asset, key) for key in keys]
    stmt = apply_filters(select(*columns, func.count()).select_from(Asset), filters).group_by(
        func.grouping_sets(*(tuple_(c) for c in columns))
    )
    counts: dict[str, list[tuple[str, int]]] = {key: [] for key in keys}
    for row in await session.execute(stmt):
        for key, value in zip(keys, row[:-1], strict=True):
            if value is not None:
                counts[key].append((str(value), int(row[-1])))
    for values in counts.values():
        values.sort(key=lambda item: (-item[1], item[0]))
        del values[FACET_VALUE_LIMIT:]
    return counts


async def facets(
    session: AsyncSession, filters: AssetFilters | None = None
) -> list[dict[str, Any]]:
    """Enum facets with counts plus duration (range) and capture date (date) facets.

    All active filters and the search query apply. An enum facet ignores its own filter so its
    other values stay selectable (``camera_model=A`` still lists camera B with its count).
    Facets without an active own filter share one table pass; each facet with one needs its own.
    """
    active = filters or AssetFilters()
    shared = [k for k in FACET_LABELS if not _own_filter_set(active, k)]
    counts = await _enum_counts(session, shared, active)
    for key in FACET_LABELS:
        if key not in counts:
            counts.update(await _enum_counts(session, [key], replace(active, **cast_kwargs(key))))
    result: list[dict[str, Any]] = []
    for key, (de, en) in FACET_LABELS.items():
        values = [{"value": v, "count": c} for v, c in counts[key]]
        if values:
            result.append(
                {"key": key, "label_de": de, "label_en": en, "kind": "enum", "values": values}
            )
    result += await _tag_facets(session, active)
    ranges = select(
        func.min(Asset.duration_ms),
        func.max(Asset.duration_ms),
        func.min(Asset.capture_at),
        func.max(Asset.capture_at),
    )
    low, high, first, last = (await session.execute(apply_filters(ranges, active))).one()
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


async def _tag_counts(
    session: AsyncSession, fields: Sequence[TaxField], filters: AssetFilters
) -> dict[UUID, list[tuple[str, int]]]:
    """Approved tag value counts of ``fields`` under ``filters`` in one pass."""
    if not fields:
        return {}
    stmt = (
        select(AssetTag.field_id, TaxValue.value, func.count(func.distinct(Asset.id)))
        .select_from(Asset)
        .join(AssetTag, AssetTag.asset_id == Asset.id)
        .join(TaxValue, TaxValue.id == AssetTag.value_id)
        .where(AssetTag.field_id.in_([f.id for f in fields]), TaxValue.status == "approved")
    )
    stmt = apply_filters(stmt, filters).group_by(AssetTag.field_id, TaxValue.value)
    counts: dict[UUID, list[tuple[str, int]]] = {f.id: [] for f in fields}
    for field_id, value, count in await session.execute(stmt):
        counts[field_id].append((value, int(count)))
    for values in counts.values():
        values.sort(key=lambda item: (-item[1], item[0]))
        del values[FACET_VALUE_LIMIT:]
    return counts


async def _tag_facets(session: AsyncSession, active: AssetFilters) -> list[dict[str, Any]]:
    """One enum facet per taxonomy field that has approved, used values (value = ``value``)."""
    fields = (
        (await session.execute(select(TaxField).order_by(TaxField.sort, TaxField.key)))
        .scalars()
        .all()
    )
    filtered = {f.key for f in fields if any(t.startswith(f.key + ":") for t in active.tags)}
    counts = await _tag_counts(session, [f for f in fields if f.key not in filtered], active)
    for field in fields:
        if field.key in filtered:
            own = replace(
                active, tags=tuple(t for t in active.tags if not t.startswith(field.key + ":"))
            )
            counts.update(await _tag_counts(session, [field], own))
    result: list[dict[str, Any]] = []
    for field in fields:
        values = [{"value": f"{field.key}:{v}", "count": c} for v, c in counts[field.id]]
        if values:
            result.append(
                {
                    "key": f"tag.{field.key}",
                    "label_de": field.label_de,
                    "label_en": field.label_en,
                    "kind": "enum",
                    "values": values,
                }
            )
    return result


async def storage_stats(session: AsyncSession) -> dict[str, Any]:
    """Storage totals by media type and capture year, plus duplicate waste (one table pass)."""
    year = func.extract("year", func.coalesce(Asset.capture_at, Asset.created_at))
    rows = await session.execute(
        select(
            Asset.media_type,
            year.cast(Integer),
            Asset.status == "duplicate",
            func.coalesce(func.sum(Asset.size_total), 0),
            func.count(),
        )
        .where(Asset.status != "trashed")
        .group_by(Asset.media_type, year.cast(Integer), Asset.status == "duplicate")
    )
    by_type: dict[str, list[int]] = {}
    by_year: dict[int, list[int]] = {}
    duplicates = [0, 0]
    for media_type, y, is_duplicate, size, count in rows:
        if is_duplicate:
            duplicates[0] += int(count)
            duplicates[1] += int(size)
            continue
        for bucket in (by_type.setdefault(media_type, [0, 0]), by_year.setdefault(int(y), [0, 0])):
            bucket[0] += int(size)
            bucket[1] += int(count)
    return {
        "storage": {
            "total_bytes": sum(v[0] for v in by_type.values()),
            "by_media_type": [
                {"key": k, "bytes": v[0], "count": v[1]} for k, v in sorted(by_type.items())
            ],
            "by_year": [
                {"year": k, "bytes": v[0], "count": v[1]} for k, v in sorted(by_year.items())
            ],
        },
        "duplicates": {"count": duplicates[0], "wasted_bytes": duplicates[1]},
    }
