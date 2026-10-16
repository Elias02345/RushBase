"""Needs lists (spec section 11): wanted footage, items and candidate assets."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rushbase_server.models.base import Base, enum_check, now_column, pk

NEED_LIST_STATUSES = ("open", "closed")
NEED_ITEM_STATUSES = ("open", "fulfilled", "dropped")
NEED_CANDIDATE_STATUSES = ("candidate", "confirmed", "rejected")
NEED_CANDIDATE_SOURCES = ("auto", "claude", "human")
NEED_CREATORS = ("user", "mcp")


class NeedList(Base):
    """A list of footage still wanted for a purpose."""

    __tablename__ = "need_list"

    id: Mapped[UUID] = pk()
    title: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, server_default="", default="")
    status: Mapped[str] = mapped_column(String(8), server_default="open", default="open")
    created_by_kind: Mapped[str] = mapped_column(String(8))
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (
        enum_check("status", NEED_LIST_STATUSES),
        enum_check("created_by_kind", NEED_CREATORS),
        Index("ix_need_list_status", "status"),
    )


class NeedItem(Base):
    """One wanted shot: a description, filters and how many matches are wanted."""

    __tablename__ = "need_item"

    id: Mapped[UUID] = pk()
    list_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("need_list.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    description: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    count_wanted: Mapped[int] = mapped_column(Integer, server_default="1", default=1)
    status: Mapped[str] = mapped_column(String(10), server_default="open", default="open")
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (
        enum_check("status", NEED_ITEM_STATUSES),
        Index("ix_need_item_list_id", "list_id"),
        Index("ix_need_item_status", "status"),
    )


class NeedCandidate(Base):
    """An asset (or shot) that may fulfil an item."""

    __tablename__ = "need_candidate"

    id: Mapped[UUID] = pk()
    item_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("need_item.id", ondelete="CASCADE"))
    asset_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("asset.id", ondelete="CASCADE"))
    shot_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("shot.id", ondelete="SET NULL"))
    score: Mapped[float] = mapped_column(Float, server_default="0", default=0.0)
    status: Mapped[str] = mapped_column(String(10), server_default="candidate", default="candidate")
    source: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (
        enum_check("status", NEED_CANDIDATE_STATUSES),
        enum_check("source", NEED_CANDIDATE_SOURCES),
        Index("ux_need_candidate_item_asset", "item_id", "asset_id", unique=True),
        Index("ix_need_candidate_asset_id", "asset_id"),
    )
