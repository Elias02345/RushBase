"""Assets, their files and metadata, shots, filing rules and the move journal."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from rushbase_core import FileRole, MediaType
from rushbase_server.models.base import Base, enum_check, now_column, pk

ASSET_STATUSES = (
    "ingesting",
    "processing",
    "review",
    "filed",
    "quarantined",
    "trashed",
    "duplicate",
)
ORIENTATIONS = ("landscape", "portrait", "square")
DECODE_STATUSES = ("ok", "proxy_only", "metadata_only", "decoder_missing", "decoder_error")
TRISTATE = ("yes", "no", "unknown")
REVIEW_STATES = ("none", "pending", "done")
MEDIA_TYPES = tuple(m.value for m in MediaType)
FILE_ROLES = tuple(r.value for r in FileRole)


class FilingRule(Base):
    """A versioned filing template. Exactly one rule is active at a time."""

    __tablename__ = "filing_rule"

    id: Mapped[UUID] = pk()
    version: Mapped[int] = mapped_column(Integer, unique=True)
    template: Mapped[str] = mapped_column(Text)
    fallbacks: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL")
    )
    note: Mapped[str] = mapped_column(Text, server_default="", default="")
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        Index("ux_filing_rule_active", "active", unique=True, postgresql_where=text("active")),
    )


class Asset(Base):
    """A logical media item that may consist of several files."""

    __tablename__ = "asset"

    id: Mapped[UUID] = pk()
    batch_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("batch.id", ondelete="SET NULL"))
    media_type: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), server_default="ingesting", default="ingesting")
    duplicate_of: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("asset.id"))
    # No foreign key: asset and asset_file reference each other; the service keeps it consistent.
    primary_file_id: Mapped[UUID | None] = mapped_column(Uuid)
    content_rel_dir: Mapped[str | None] = mapped_column(Text)  # set once filed, never changed
    capture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_tz: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    codec: Mapped[str | None] = mapped_column(Text)
    camera_make: Mapped[str | None] = mapped_column(Text)
    camera_model: Mapped[str | None] = mapped_column(Text)
    lens_model: Mapped[str | None] = mapped_column(Text)
    focal_length_mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    focal_length_35mm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    iso: Mapped[int | None] = mapped_column(Integer)
    log_profile: Mapped[str | None] = mapped_column(Text)
    orientation: Mapped[str | None] = mapped_column(String(10))
    gps_lat: Mapped[float | None] = mapped_column(Double)
    gps_lon: Mapped[float | None] = mapped_column(Double)
    gps_alt: Mapped[float | None] = mapped_column(Double)
    size_total: Mapped[int] = mapped_column(BigInteger, server_default="0", default=0)
    decode_status: Mapped[str] = mapped_column(String(16), server_default="ok", default="ok")
    # Derived files of the preview stage: ``files`` and ``normalization``.
    derived: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    perceptual_hash: Mapped[str | None] = mapped_column(Text)
    tech_summary_de: Mapped[str | None] = mapped_column(Text)
    tech_summary_en: Mapped[str | None] = mapped_column(Text)
    # Set while the files rest in ``.rushbase/quarantine/`` (reason, stage, moved paths).
    quarantine: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    title: Mapped[str | None] = mapped_column(Text)
    summary_short: Mapped[str | None] = mapped_column(Text)
    summary_long_de: Mapped[str | None] = mapped_column(Text)
    summary_long_en: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    person_identifiable: Mapped[str] = mapped_column(
        String(8), server_default="unknown", default="unknown"
    )
    consent: Mapped[str] = mapped_column(String(8), server_default="unknown", default="unknown")
    consent_ref: Mapped[str | None] = mapped_column(Text)
    analysis_profile: Mapped[str | None] = mapped_column(Text)
    summary_short_en: Mapped[str | None] = mapped_column(Text)
    shot_size: Mapped[str | None] = mapped_column(Text)
    camera_angle: Mapped[str | None] = mapped_column(Text)
    interior_exterior: Mapped[str | None] = mapped_column(Text)
    time_of_day: Mapped[str | None] = mapped_column(Text)
    people_count: Mapped[int | None] = mapped_column(Integer)
    ai_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    audio_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    review_state: Mapped[str] = mapped_column(Text, server_default="none", default="none")
    review_reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), default=list
    )
    # Columns edited by a person; AI results never overwrite them.
    human_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), default=list
    )
    # Server-maintained by DB triggers (migration 0004); never written by the ORM.
    search_tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rule_version_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("filing_rule.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (
        enum_check("media_type", MEDIA_TYPES),
        enum_check("status", ASSET_STATUSES),
        CheckConstraint(
            "orientation IS NULL OR orientation IN ('landscape', 'portrait', 'square')",
            name="orientation",
        ),
        enum_check("decode_status", DECODE_STATUSES),
        enum_check("person_identifiable", TRISTATE),
        enum_check("consent", TRISTATE),
        enum_check("review_state", REVIEW_STATES),
        Index(
            "ix_asset_review_state", "review_state", postgresql_where=text("review_state <> 'none'")
        ),
        CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="rating"),
        Index("ix_asset_status", "status"),
        Index("ix_asset_media_type_capture_at", "media_type", "capture_at"),
        Index("ix_asset_camera_model", "camera_model"),
        Index("ix_asset_capture_at", "capture_at"),
        Index("ix_asset_lens_model", "lens_model"),
        Index("ix_asset_log_profile", "log_profile"),
        Index("ix_asset_orientation", "orientation"),
        Index("ix_asset_codec", "codec"),
        Index("ix_asset_duplicate_of", "duplicate_of"),
        Index("ix_asset_batch_id", "batch_id"),
        Index("ix_asset_search_tsv", "search_tsv", postgresql_using="gin"),
    )


class AssetFile(Base):
    """One file of an asset. ``rel_path`` follows the file when it is filed."""

    __tablename__ = "asset_file"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    rel_path: Mapped[str] = mapped_column(Text, unique=True)
    original_rel_path: Mapped[str] = mapped_column(Text)  # immutable Input path
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blake3: Mapped[str | None] = mapped_column(Text)
    quick_fp: Mapped[str | None] = mapped_column(Text)
    card_structure_root: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        enum_check("role", FILE_ROLES),
        Index("ix_asset_file_asset_id", "asset_id"),
        Index("ix_asset_file_blake3", "blake3"),
        Index("ix_asset_file_quick_fp", "quick_fp"),
        Index(
            "ix_asset_file_card_structure_root",
            "card_structure_root",
            postgresql_where=text("card_structure_root IS NOT NULL"),
        ),
        Index(
            "ix_asset_file_rel_path_trgm",
            "rel_path",
            postgresql_using="gin",
            postgresql_ops={"rel_path": "gin_trgm_ops"},
        ),
    )


class AssetMetadata(Base):
    """Normalized and raw technical metadata of an asset."""

    __tablename__ = "asset_metadata"

    asset_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True
    )
    normalized: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    raw: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1", default=1)
    updated_at: Mapped[datetime] = now_column(onupdate=True)


class Shot(Base):
    """A detected shot (scene) of a video asset."""

    __tablename__ = "shot"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    keyframes: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), default=list
    )
    camera_motion: Mapped[str | None] = mapped_column(Text)
    sharpness: Mapped[float | None] = mapped_column(Double)
    shake: Mapped[float | None] = mapped_column(Double)
    exposure: Mapped[float | None] = mapped_column(Double)
    noise: Mapped[float | None] = mapped_column(Double)
    description_short: Mapped[str | None] = mapped_column(Text)
    description_long_de: Mapped[str | None] = mapped_column(Text)
    description_long_en: Mapped[str | None] = mapped_column(Text)
    shot_size: Mapped[str | None] = mapped_column(Text)
    angle: Mapped[str | None] = mapped_column(Text)
    description_short_de: Mapped[str | None] = mapped_column(Text)
    description_short_en: Mapped[str | None] = mapped_column(Text)
    interior_exterior: Mapped[str | None] = mapped_column(Text)
    time_of_day: Mapped[str | None] = mapped_column(Text)
    people_count: Mapped[int | None] = mapped_column(Integer)
    person_identifiable: Mapped[str] = mapped_column(
        Text, server_default="unknown", default="unknown"
    )
    ai_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    human_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), default=list
    )
    # Server-maintained by a DB trigger (migration 0004); never written by the ORM.
    search_tsv: Mapped[str | None] = mapped_column(TSVECTOR)

    __table_args__ = (
        enum_check("person_identifiable", TRISTATE),
        UniqueConstraint("asset_id", "idx", name="uq_shot_asset_id_idx"),
        Index("ix_shot_search_tsv", "search_tsv", postgresql_using="gin"),
    )


class MoveJournal(Base):
    """Database mirror of ``.rushbase/journal/*.jsonl``: every move of a file in the library."""

    __tablename__ = "move_journal"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="SET NULL"))
    from_rel: Mapped[str] = mapped_column(Text)
    to_rel: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = now_column()
    reason: Mapped[str] = mapped_column(Text)

    __table_args__ = (Index("ix_move_journal_asset_id", "asset_id"),)


__all__ = [
    "Asset",
    "AssetFile",
    "AssetMetadata",
    "FilingRule",
    "MoveJournal",
    "Shot",
]
