"""M4 models: taxonomy, tags, transcripts, audio events, embeddings, golden labels and eval runs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from rushbase_server.models.base import Base, enum_check, now_column, pk

TAG_SOURCES = ("ai", "claude", "human")
TAG_PRECEDENCE = {"ai": 0, "claude": 1, "human": 2}
TAX_STATUSES = ("proposed", "approved", "deprecated")
EMBEDDING_DIM = 768


class Vector(UserDefinedType[list[float]]):
    """pgvector ``vector(n)``; values travel as the text form ``[a,b,c]``."""

    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: list[float] | None) -> str | None:
            return None if value is None else "[" + ",".join(f"{x:.8g}" for x in value) + "]"

        return process


class TaxField(Base):
    """A taxonomy field (location type, activity, mood ...)."""

    __tablename__ = "tax_field"

    id: Mapped[UUID] = pk()
    key: Mapped[str] = mapped_column(Text, unique=True)
    label_de: Mapped[str] = mapped_column(Text)
    label_en: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, server_default="single", default="single")
    description: Mapped[str] = mapped_column(Text, server_default="", default="")
    sort: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (enum_check("kind", ("single", "multi")),)


class TaxValue(Base):
    """A value of a field: approved values feed search and the worker snapshot."""

    __tablename__ = "tax_value"

    id: Mapped[UUID] = pk()
    field_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tax_field.id", ondelete="CASCADE"))
    value: Mapped[str] = mapped_column(Text)
    label_de: Mapped[str | None] = mapped_column(Text)
    label_en: Mapped[str | None] = mapped_column(Text)
    synonyms: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=sql_text("'{}'"), default=list
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("tax_value.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, server_default="proposed", default="proposed")
    description: Mapped[str] = mapped_column(Text, server_default="", default="")
    proposal_count: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    example_asset_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid), server_default=sql_text("'{}'"), default=list
    )
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (
        enum_check("status", TAX_STATUSES),
        Index("ux_tax_value_field_value", "field_id", func.lower(sql_text("value")), unique=True),
    )


class TaxonomyVersion(Base):
    """Single-row counter, bumped whenever approved values change."""

    __tablename__ = "taxonomy_version"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default="1", default=1)
    version: Mapped[int] = mapped_column(Integer, server_default="1", default=1)
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (CheckConstraint("id = 1", name="single"),)


class AssetTag(Base):
    """A tag of an asset or shot; precedence human > claude > ai."""

    __tablename__ = "asset_tag"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    shot_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("shot.id", ondelete="CASCADE"))
    field_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tax_field.id", ondelete="CASCADE"))
    value_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tax_value.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    model_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        enum_check("source", TAG_SOURCES),
        Index(
            "ux_asset_tag_unique",
            "asset_id",
            "shot_id",
            "value_id",
            "source",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_asset_tag_value_id", "value_id"),
        Index("ix_asset_tag_asset_id", "asset_id"),
    )


class TranscriptSegment(Base):
    """One transcript segment with word timings; ``tsv`` is generated by the database."""

    __tablename__ = "transcript_segment"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    speaker: Mapped[str | None] = mapped_column(Text)
    words: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb"), default=list
    )
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('rushbase_simple'::regconfig, text)", persisted=True)
    )

    __table_args__ = (
        UniqueConstraint("asset_id", "idx", name="uq_transcript_segment_asset_id_idx"),
        Index("ix_transcript_segment_tsv", "tsv", postgresql_using="gin"),
    )


class AudioEvent(Base):
    """A grouped audio event (gunshot, explosion, applause ...)."""

    __tablename__ = "audio_event"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    tag: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    confidence: Mapped[float] = mapped_column(Float)
    raw_top: Mapped[list[Any]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb"), default=list
    )

    __table_args__ = (
        Index("ix_audio_event_asset_id", "asset_id"),
        Index("ix_audio_event_tag", "tag"),
    )


class EmbeddingImage(Base):
    """SigLIP vector of a keyframe or photo (cross-modal space)."""

    __tablename__ = "embedding_image"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    shot_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("shot.id", ondelete="CASCADE"))
    keyframe: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        Index(
            "ix_embedding_image_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_embedding_image_asset_id", "asset_id"),
        Index("ix_embedding_image_shot_id", "shot_id"),
    )


class EmbeddingText(Base):
    """e5 vector of a description or transcript chunk (text space)."""

    __tablename__ = "embedding_text"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    shot_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("shot.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int | None] = mapped_column(BigInteger)
    end_ms: Mapped[int | None] = mapped_column(BigInteger)
    language: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        Index(
            "ix_embedding_text_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_embedding_text_asset_id", "asset_id"),
        Index("ix_embedding_text_shot_id", "shot_id"),
    )


class GoldenLabel(Base):
    """Expected value of a field for an asset or shot (evaluation ground truth)."""

    __tablename__ = "golden_label"

    id: Mapped[UUID] = pk()
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    shot_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("shot.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    labeller_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL")
    )
    origin: Mapped[str] = mapped_column(Text, server_default="label", default="label")
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        enum_check("origin", ("label", "correction")),
        Index(
            "ux_golden_label_unique",
            "asset_id",
            "shot_id",
            "field",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class EvalRun(Base):
    """One evaluation of the latest AI results against the golden labels."""

    __tablename__ = "eval_run"

    id: Mapped[UUID] = pk()
    created_at: Mapped[datetime] = now_column()
    label: Mapped[str | None] = mapped_column(Text)
    model_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )
    golden_count: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=sql_text("'{}'::jsonb"), default=dict
    )


class Collection(Base):
    """A named, manually curated set of assets (virtual organisation, no files move)."""

    __tablename__ = "collection"

    id: Mapped[UUID] = pk()
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (Index("ux_collection_name", func.lower(sql_text("name")), unique=True),)


class CollectionAsset(Base):
    """Membership of an asset in a collection."""

    __tablename__ = "collection_asset"

    collection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("collection.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = now_column()

    __table_args__ = (Index("ix_collection_asset_asset_id", "asset_id"),)
