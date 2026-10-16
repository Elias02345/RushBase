"""OAuth 2.1 clients, authorization codes, tokens and the MCP call audit (ADR-0010)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rushbase_server.models.base import Base, enum_check, now_column, pk

CLIENT_KINDS = ("dcr", "cimd")
OAUTH_TOKEN_KINDS = ("access", "refresh")


class OAuthClient(Base):
    """A registered (DCR) or metadata-document (CIMD) public client."""

    __tablename__ = "oauth_client"

    id: Mapped[UUID] = pk()
    client_id: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(Text)
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    metadata_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_etag: Mapped[str | None] = mapped_column(Text)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (enum_check("kind", CLIENT_KINDS),)


class OAuthCode(Base):
    """A single-use authorization code (stored hashed) bound to PKCE and the resource."""

    __tablename__ = "oauth_code"

    id: Mapped[UUID] = pk()
    code_hash: Mapped[str] = mapped_column(Text, unique=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("oauth_client.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"))
    redirect_uri: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    resource: Mapped[str] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (Index("ix_oauth_code_expires_at", "expires_at"),)


class OAuthToken(Base):
    """An opaque access or refresh token (stored hashed); ``family_id`` groups a grant."""

    __tablename__ = "oauth_token"

    id: Mapped[UUID] = pk()
    family_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(8))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("oauth_client.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    resource: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_column()

    __table_args__ = (
        enum_check("kind", OAUTH_TOKEN_KINDS),
        Index("ix_oauth_token_family_id", "family_id"),
        Index("ix_oauth_token_client_id", "client_id"),
        Index("ix_oauth_token_expires_at", "expires_at"),
    )


class McpAudit(Base):
    """One MCP tool call: who, which tool, hashed arguments, duration and result size."""

    __tablename__ = "mcp_audit"

    id: Mapped[UUID] = pk()
    at: Mapped[datetime] = now_column()
    client_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("oauth_client.id", ondelete="SET NULL")
    )
    client_name: Mapped[str] = mapped_column(Text)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("user.id", ondelete="SET NULL"))
    tool: Mapped[str] = mapped_column(Text)
    args_hash: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer)
    result_chars: Mapped[int] = mapped_column(BigInteger)
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), default=dict
    )

    __table_args__ = (
        Index("ix_mcp_audit_at", text("at DESC")),
        Index("ix_mcp_audit_client_id", "client_id"),
        Index("ix_mcp_audit_tool", "tool"),
    )
