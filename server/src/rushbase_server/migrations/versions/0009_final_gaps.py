"""Final gaps: "used in project" feedback (``asset_usage``) and personal token expiry.

Additive and idempotent; ``downgrade`` removes exactly these objects.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = (
    "ALTER TABLE api_token ADD COLUMN IF NOT EXISTS expires_at timestamptz",
    """
    CREATE TABLE IF NOT EXISTS asset_usage (
        asset_id uuid NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
        project_name text NOT NULL,
        source varchar(8) NOT NULL,
        machine text NOT NULL DEFAULT '',
        first_seen timestamptz NOT NULL DEFAULT now(),
        last_seen timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_asset_usage PRIMARY KEY (asset_id, project_name, source),
        CONSTRAINT ck_asset_usage_source CHECK (source IN ('resolve', 'manual', 'claude'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_asset_usage_project_name ON asset_usage (project_name)",
)


def upgrade() -> None:
    """Add ``api_token.expires_at`` and create ``asset_usage``."""
    for statement in _DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop ``asset_usage`` and ``api_token.expires_at``."""
    op.execute("DROP TABLE IF EXISTS asset_usage")
    op.execute("ALTER TABLE api_token DROP COLUMN IF EXISTS expires_at")
