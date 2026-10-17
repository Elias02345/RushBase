"""M7: enrollment token revocation and indexes found by the 100 000 asset scale test.

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

# Keyset pagination: one index per sort option of the asset list (expression and tie breaker as
# used by ``services.catalog.SORTS``), plus the audit lookup of the asset detail view.
_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_asset_sort_date", "ON asset ((coalesce(capture_at, created_at)) DESC, id DESC)"),
    ("ix_asset_sort_created", "ON asset (created_at DESC, id DESC)"),
    ("ix_asset_sort_size", "ON asset (size_total DESC, id DESC)"),
    ("ix_asset_sort_duration", "ON asset ((coalesce(duration_ms, -1)) DESC, id DESC)"),
    ("ix_audit_log_target", "ON audit_log (target_kind, target_id)"),
)


# HNSW on 16-bit vectors: half the index size (so it stays in RAM), the same recall in practice.
# Queries order by ``embedding::halfvec(768)``; the full-precision indexes of 0005 are replaced.
_VECTOR_TABLES = ("embedding_image", "embedding_text")


def _half(table: str) -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS ix_{table}_hnsw_half ON {table} "
        "USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops)"
    )


def upgrade() -> None:
    """Add ``enrollment_token.revoked_at``, the performance indexes and the halfvec indexes."""
    op.execute("ALTER TABLE enrollment_token ADD COLUMN IF NOT EXISTS revoked_at timestamptz")
    for table in _VECTOR_TABLES:
        op.execute(_half(table))
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_hnsw")
    for name, definition in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} {definition}")


def downgrade() -> None:
    """Drop the indexes and the column again."""
    for table in _VECTOR_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_hnsw ON {table} "
            "USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_hnsw_half")
    for name, _definition in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("ALTER TABLE enrollment_token DROP COLUMN IF EXISTS revoked_at")
