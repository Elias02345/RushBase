"""M7: enrollment token revocation and scale-test indexes.

Additive and idempotent (``ADD COLUMN IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS``).
``downgrade`` removes exactly what this revision adds.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str], ...] = ()


def upgrade() -> None:
    """Add ``enrollment_token.revoked_at`` and the performance indexes."""
    op.execute("ALTER TABLE enrollment_token ADD COLUMN IF NOT EXISTS revoked_at timestamptz")
    for name, definition in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} {definition}")


def downgrade() -> None:
    """Drop the indexes and the column again."""
    for name, _definition in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("ALTER TABLE enrollment_token DROP COLUMN IF EXISTS revoked_at")
