"""Spec gaps: trash items (delete to trash) and audio/video sync suggestions.

Additive and idempotent (``CREATE TABLE IF NOT EXISTS``); ``downgrade`` drops exactly these
tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS trash_item (
        asset_id uuid PRIMARY KEY REFERENCES asset(id) ON DELETE CASCADE,
        trashed_at timestamptz NOT NULL DEFAULT now(),
        trashed_by uuid REFERENCES "user"(id) ON DELETE SET NULL,
        prior_status varchar(16) NOT NULL,
        manifest jsonb NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_trash_item_trashed_at ON trash_item (trashed_at)",
    """
    CREATE TABLE IF NOT EXISTS sync_suggestion (
        id uuid PRIMARY KEY,
        asset_a uuid NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
        asset_b uuid NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
        offset_ms bigint NOT NULL,
        confidence double precision NOT NULL,
        basis varchar(16) NOT NULL,
        status varchar(10) NOT NULL DEFAULT 'suggested',
        decided_by uuid REFERENCES "user"(id) ON DELETE SET NULL,
        decided_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_sync_suggestion_pair UNIQUE (asset_a, asset_b),
        CONSTRAINT ck_sync_suggestion_status
            CHECK (status IN ('suggested', 'confirmed', 'rejected')),
        CONSTRAINT ck_sync_suggestion_basis CHECK (basis IN ('timecode', 'capture_at'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_sync_suggestion_asset_a ON sync_suggestion (asset_a)",
    "CREATE INDEX IF NOT EXISTS ix_sync_suggestion_asset_b ON sync_suggestion (asset_b)",
)


def upgrade() -> None:
    """Create ``trash_item`` and ``sync_suggestion``."""
    for statement in _DDL:
        op.execute(statement)


def downgrade() -> None:
    """Drop both tables."""
    op.execute("DROP TABLE IF EXISTS sync_suggestion")
    op.execute("DROP TABLE IF EXISTS trash_item")
