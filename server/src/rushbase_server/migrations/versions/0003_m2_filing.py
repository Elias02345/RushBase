"""M2: decode status, derived files, summaries, quarantine record and facet indexes.

Additive: only adds nullable or defaulted columns and indexes; every statement is guarded so a
re-run after a partial failure is safe. ``downgrade`` removes exactly what this revision added.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECODE_STATUSES = ("ok", "proxy_only", "metadata_only", "decoder_missing", "decoder_error")
CHECK_NAME = "ck_asset_decode_status"


def _columns() -> list[sa.Column[object]]:
    return [
        sa.Column("decode_status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column(
            "derived",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("perceptual_hash", sa.Text(), nullable=True),
        sa.Column("tech_summary_de", sa.Text(), nullable=True),
        sa.Column("tech_summary_en", sa.Text(), nullable=True),
        sa.Column("quarantine", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    ]


# (name, table, columns, extra keyword arguments)
_INDEXES: tuple[tuple[str, str, list[str], dict[str, object]], ...] = (
    ("ix_asset_capture_at", "asset", ["capture_at"], {}),
    ("ix_asset_lens_model", "asset", ["lens_model"], {}),
    ("ix_asset_log_profile", "asset", ["log_profile"], {}),
    ("ix_asset_orientation", "asset", ["orientation"], {}),
    ("ix_asset_codec", "asset", ["codec"], {}),
    ("ix_asset_duplicate_of", "asset", ["duplicate_of"], {}),
    (
        "ix_asset_file_card_structure_root",
        "asset_file",
        ["card_structure_root"],
        {"postgresql_where": sa.text("card_structure_root IS NOT NULL")},
    ),
    (
        "ix_asset_file_rel_path_trgm",
        "asset_file",
        ["rel_path"],
        {"postgresql_using": "gin", "postgresql_ops": {"rel_path": "gin_trgm_ops"}},
    ),
)


def _asset_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("asset")}


def _check_exists() -> bool:
    checks = sa.inspect(op.get_bind()).get_check_constraints("asset")
    return any(c["name"] == CHECK_NAME for c in checks)


def upgrade() -> None:
    """Add the M2 columns and indexes."""
    existing = _asset_columns()
    for column in _columns():
        if column.name not in existing:
            op.add_column("asset", column)
    if not _check_exists():
        quoted = ", ".join(f"'{value}'" for value in DECODE_STATUSES)
        op.create_check_constraint(op.f(CHECK_NAME), "asset", f"decode_status IN ({quoted})")
    for name, table, columns, kwargs in _INDEXES:
        op.create_index(name, table, columns, if_not_exists=True, **kwargs)  # type: ignore[arg-type]


def downgrade() -> None:
    """Remove what :func:`upgrade` added."""
    for name, table, _cols, _kwargs in reversed(_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
    if _check_exists():
        op.drop_constraint(op.f(CHECK_NAME), "asset", type_="check")
    existing = _asset_columns()
    for column in reversed(_columns()):
        if column.name in existing:
            op.drop_column("asset", column.name)
