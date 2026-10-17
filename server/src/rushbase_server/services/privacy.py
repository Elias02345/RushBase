"""Privacy tools: find the assets of a batch or of a person and lock them.

Rushbase does no face recognition (it is off by default and not implemented). A "person" is a
label: an approved value of the taxonomy field ``person`` (value, German/English label or
synonym), or a name that occurs in an asset's title, descriptions or transcript. The lookup is a
text search, so the result is a candidate list a person confirms by locking it.
"""

from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import Asset, AssetTag, TaxField, TaxValue, TranscriptSegment

PERSON_FIELD = "person"
MAX_LOCK = 5000
SEP = "\x1f"


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def person_condition(label: str) -> ColumnElement[bool]:
    """SQL condition: the asset is tagged with the person or mentions the label in its texts."""
    wanted = label.strip().lower()
    values = (
        select(TaxValue.id)
        .join(TaxField, TaxField.id == TaxValue.field_id)
        .where(
            TaxField.key == PERSON_FIELD,
            TaxValue.status == "approved",
            or_(
                func.lower(TaxValue.value) == wanted,
                func.lower(func.coalesce(TaxValue.label_de, "")) == wanted,
                func.lower(func.coalesce(TaxValue.label_en, "")) == wanted,
                func.lower(SEP + func.array_to_string(TaxValue.synonyms, SEP) + SEP).like(
                    f"%{SEP}{_like(wanted)[1:-1]}{SEP}%", escape="\\"
                ),
            ),
        )
    )
    pattern = _like(wanted)
    texts = or_(
        *(
            func.lower(func.coalesce(column, "")).like(pattern, escape="\\")
            for column in (
                Asset.title,
                Asset.summary_short,
                Asset.summary_short_en,
                Asset.summary_long_de,
                Asset.summary_long_en,
            )
        ),
        Asset.id.in_(
            select(TranscriptSegment.asset_id).where(
                func.lower(TranscriptSegment.text).like(pattern, escape="\\")
            )
        ),
    )
    return or_(Asset.id.in_(select(AssetTag.asset_id).where(AssetTag.value_id.in_(values))), texts)


async def find_assets(
    session: AsyncSession, *, batch_id: UUID | None = None, person: str | None = None
) -> list[Asset]:
    """Assets of ``batch_id`` or matching ``person`` (exactly one), without trashed ones."""
    if (batch_id is None) == (not person or not person.strip()):
        raise ValueError("give either a batch or a person")
    condition = (
        Asset.batch_id == batch_id if batch_id is not None else person_condition(person or "")
    )
    stmt = (
        select(Asset)
        .where(and_(condition, Asset.status != "trashed"))
        .order_by(Asset.id)
        .limit(MAX_LOCK + 1)
    )
    return list((await session.execute(stmt)).scalars().all())
