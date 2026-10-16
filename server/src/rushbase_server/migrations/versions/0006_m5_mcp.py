"""M5: OAuth clients, codes and tokens, MCP call audit, needs lists.

Additive and idempotent (``CREATE TABLE IF NOT EXISTS``); ``downgrade`` drops exactly these tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "mcp_audit",
    "oauth_token",
    "oauth_code",
    "oauth_client",
    "need_candidate",
    "need_item",
    "need_list",
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS oauth_client (
        id uuid PRIMARY KEY,
        client_id text NOT NULL,
        kind varchar(8) NOT NULL,
        name text NOT NULL,
        redirect_uris text[] NOT NULL,
        scopes text[] NOT NULL DEFAULT '{}'::text[],
        metadata_expires_at timestamptz,
        metadata_etag text,
        rate_limit_per_minute integer,
        last_used_at timestamptz,
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_oauth_client_client_id UNIQUE (client_id),
        CONSTRAINT ck_oauth_client_kind CHECK (kind IN ('dcr', 'cimd'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_code (
        id uuid PRIMARY KEY,
        code_hash text NOT NULL,
        client_id uuid NOT NULL REFERENCES oauth_client(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        redirect_uri text NOT NULL,
        scopes text[] NOT NULL,
        resource text NOT NULL,
        code_challenge text NOT NULL,
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_oauth_code_code_hash UNIQUE (code_hash)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_oauth_code_expires_at ON oauth_code (expires_at)",
    """
    CREATE TABLE IF NOT EXISTS oauth_token (
        id uuid PRIMARY KEY,
        family_id uuid NOT NULL,
        kind varchar(8) NOT NULL,
        token_hash text NOT NULL,
        client_id uuid NOT NULL REFERENCES oauth_client(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
        scopes text[] NOT NULL,
        resource text NOT NULL,
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_oauth_token_token_hash UNIQUE (token_hash),
        CONSTRAINT ck_oauth_token_kind CHECK (kind IN ('access', 'refresh'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_oauth_token_family_id ON oauth_token (family_id)",
    "CREATE INDEX IF NOT EXISTS ix_oauth_token_client_id ON oauth_token (client_id)",
    "CREATE INDEX IF NOT EXISTS ix_oauth_token_expires_at ON oauth_token (expires_at)",
    """
    CREATE TABLE IF NOT EXISTS mcp_audit (
        id uuid PRIMARY KEY,
        at timestamptz NOT NULL DEFAULT now(),
        client_id uuid REFERENCES oauth_client(id) ON DELETE SET NULL,
        client_name text NOT NULL,
        user_id uuid REFERENCES "user"(id) ON DELETE SET NULL,
        tool text NOT NULL,
        args_hash varchar(64) NOT NULL,
        duration_ms integer NOT NULL,
        result_chars bigint NOT NULL,
        ok boolean NOT NULL,
        error text,
        details jsonb NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_mcp_audit_at ON mcp_audit (at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_mcp_audit_client_id ON mcp_audit (client_id)",
    "CREATE INDEX IF NOT EXISTS ix_mcp_audit_tool ON mcp_audit (tool)",
    """
    CREATE TABLE IF NOT EXISTS need_list (
        id uuid PRIMARY KEY,
        title text NOT NULL,
        purpose text NOT NULL DEFAULT '',
        status varchar(8) NOT NULL DEFAULT 'open',
        created_by_kind varchar(8) NOT NULL,
        created_by_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_need_list_status CHECK (status IN ('open', 'closed')),
        CONSTRAINT ck_need_list_created_by_kind CHECK (created_by_kind IN ('user', 'mcp'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_need_list_status ON need_list (status)",
    """
    CREATE TABLE IF NOT EXISTS need_item (
        id uuid PRIMARY KEY,
        list_id uuid NOT NULL REFERENCES need_list(id) ON DELETE CASCADE,
        position integer NOT NULL DEFAULT 0,
        description text NOT NULL,
        filters jsonb NOT NULL DEFAULT '{}'::jsonb,
        count_wanted integer NOT NULL DEFAULT 1,
        status varchar(10) NOT NULL DEFAULT 'open',
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_need_item_status CHECK (status IN ('open', 'fulfilled', 'dropped'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_need_item_list_id ON need_item (list_id)",
    "CREATE INDEX IF NOT EXISTS ix_need_item_status ON need_item (status)",
    """
    CREATE TABLE IF NOT EXISTS need_candidate (
        id uuid PRIMARY KEY,
        item_id uuid NOT NULL REFERENCES need_item(id) ON DELETE CASCADE,
        asset_id uuid NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
        shot_id uuid REFERENCES shot(id) ON DELETE SET NULL,
        score double precision NOT NULL DEFAULT 0,
        status varchar(10) NOT NULL DEFAULT 'candidate',
        source varchar(8) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_need_candidate_status
            CHECK (status IN ('candidate', 'confirmed', 'rejected')),
        CONSTRAINT ck_need_candidate_source CHECK (source IN ('auto', 'claude', 'human'))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_need_candidate_item_asset "
    "ON need_candidate (item_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_need_candidate_asset_id ON need_candidate (asset_id)",
)


def upgrade() -> None:
    """Create the M5 tables."""
    for statement in _DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop the M5 tables (children first)."""
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
