"""Library singleton, settings, users, sessions, API tokens and the audit log."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rushbase_server.models.base import Base, enum_check, now_column, pk

ROLES = ("admin", "editor", "viewer")
LOCALES = ("de", "en")
TOKEN_KINDS = ("worker", "personal")
ACTOR_KINDS = ("user", "worker", "mcp", "system")


class Library(Base):
    """The single library this database belongs to (``id`` equals ``library.json``)."""

    __tablename__ = "library"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    root_hint: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (Index("ux_library_singleton", text("(true)"), unique=True),)


class AppSetting(Base):
    """Runtime-adjustable key/value setting (queue pause, enabled stages, ...)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = now_column(onupdate=True)


class User(Base):
    """A person who signs in to the web UI."""

    __tablename__ = "user"

    id: Mapped[UUID] = pk()
    username: Mapped[str] = mapped_column(CITEXT, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    locale: Mapped[str] = mapped_column(String(2), server_default="de", default="de")
    disabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()
    updated_at: Mapped[datetime] = now_column(onupdate=True)

    __table_args__ = (enum_check("role", ROLES), enum_check("locale", LOCALES))


class Session(Base):
    """Cookie session. The cookie holds an opaque token; only its SHA-256 is stored."""

    __tablename__ = "session"

    id: Mapped[UUID] = pk()
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    csrf_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        Index("ix_session_user_id", "user_id"),
        Index("ix_session_expires_at", "expires_at"),
    )


class ApiToken(Base):
    """Worker API key or personal API token (stored hashed; plaintext shown once)."""

    __tablename__ = "api_token"

    id: Mapped[UUID] = pk()
    user_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"))
    worker_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("worker.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text, server_default="", default="")
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    prefix: Mapped[str] = mapped_column(String(8))
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]"), default=list
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        enum_check("kind", TOKEN_KINDS),
        CheckConstraint(
            "(kind = 'worker' AND worker_id IS NOT NULL)"
            " OR (kind = 'personal' AND user_id IS NOT NULL)",
            name="owner",
        ),
    )


class AuditLog(Base):
    """Append-only record of security-relevant actions."""

    __tablename__ = "audit_log"

    id: Mapped[UUID] = pk()
    at: Mapped[datetime] = now_column()
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(Text)
    target_kind: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )
    ip: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        enum_check("actor_kind", ACTOR_KINDS),
        Index("ix_audit_log_at", text("at DESC")),
        Index("ix_audit_log_actor", "actor_kind", "actor_id"),
        Index("ix_audit_log_target", "target_kind", "target_id"),
    )
