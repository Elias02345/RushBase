"""M1 schema: library, auth, workers, ingest, assets, shots, results, queue and filing tables.

Additive: only creates objects, every statement is guarded (``IF NOT EXISTS`` / existence
checks) so re-running after a partial failure is safe. ``downgrade`` drops exactly what this
revision created. Table design: ``docs/architecture/data-model.md``.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_table(name: str, *args: Any, **kwargs: Any) -> None:
    """Create a table unless it already exists (idempotent re-run)."""
    if not sa.inspect(op.get_bind()).has_table(name):
        op.create_table(name, *args, **kwargs)


def upgrade() -> None:
    """Create the M1 tables and indexes."""
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    _create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'worker', 'mcp', 'system')",
            name=op.f("ck_audit_log_actor_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        "ix_audit_log_actor",
        "audit_log",
        ["actor_kind", "actor_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_audit_log_at",
        "audit_log",
        [sa.literal_column("at DESC")],
        unique=False,
        if_not_exists=True,
    )
    _create_table(
        "batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("input_path", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("hint_date", sa.Date(), nullable=True),
        sa.Column("hint_location", sa.Text(), nullable=True),
        sa.Column("hint_project", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="scanning", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('scanning', 'waiting', 'processing', 'done', 'error')",
            name=op.f("ck_batch_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batch")),
        sa.UniqueConstraint("input_path", name=op.f("uq_batch_input_path")),
    )
    op.create_index("ix_batch_status", "batch", ["status"], unique=False, if_not_exists=True)
    _create_table(
        "library",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("root_hint", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_library")),
    )
    op.create_index(
        "ux_library_singleton",
        "library",
        [sa.literal_column("(true)")],
        unique=True,
        if_not_exists=True,
    )
    _create_table(
        "user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", postgresql.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=2), server_default="de", nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("locale IN ('de', 'en')", name=op.f("ck_user_locale")),
        sa.CheckConstraint("role IN ('admin', 'editor', 'viewer')", name=op.f("ck_user_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
    )
    _create_table(
        "worker",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("platform", sa.Text(), nullable=True),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="online", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_stage", sa.String(length=16), nullable=True),
        sa.Column("current_job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('online', 'offline', 'draining')", name=op.f("ck_worker_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker")),
    )
    _create_table(
        "api_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("worker_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.Text(), server_default="", nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column(
            "scopes", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'worker' AND worker_id IS NOT NULL) OR (kind = 'personal' AND user_id IS NOT NULL)",
            name=op.f("ck_api_token_owner"),
        ),
        sa.CheckConstraint("kind IN ('worker', 'personal')", name=op.f("ck_api_token_kind")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_api_token_user_id_user"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["worker.id"],
            name=op.f("fk_api_token_worker_id_worker"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_token_token_hash")),
    )
    _create_table(
        "app_setting",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["user.id"],
            name=op.f("fk_app_setting_updated_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_setting")),
    )
    _create_table(
        "enrollment_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_worker", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_enrollment_token_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["used_by_worker"],
            ["worker.id"],
            name=op.f("fk_enrollment_token_used_by_worker_worker"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollment_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_enrollment_token_token_hash")),
    )
    _create_table(
        "filing_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column(
            "fallbacks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_filing_rule_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_rule")),
        sa.UniqueConstraint("version", name=op.f("uq_filing_rule_version")),
    )
    op.create_index(
        "ux_filing_rule_active",
        "filing_rule",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
        if_not_exists=True,
    )
    _create_table(
        "session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_session_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_session_token_hash")),
    )
    op.create_index(
        "ix_session_expires_at", "session", ["expires_at"], unique=False, if_not_exists=True
    )
    op.create_index("ix_session_user_id", "session", ["user_id"], unique=False, if_not_exists=True)
    _create_table(
        "asset",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("media_type", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ingesting", nullable=False),
        sa.Column("duplicate_of", sa.Uuid(), nullable=True),
        sa.Column("primary_file_id", sa.Uuid(), nullable=True),
        sa.Column("content_rel_dir", sa.Text(), nullable=True),
        sa.Column("capture_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capture_tz", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("codec", sa.Text(), nullable=True),
        sa.Column("camera_make", sa.Text(), nullable=True),
        sa.Column("camera_model", sa.Text(), nullable=True),
        sa.Column("lens_model", sa.Text(), nullable=True),
        sa.Column("focal_length_mm", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("focal_length_35mm", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("iso", sa.Integer(), nullable=True),
        sa.Column("log_profile", sa.Text(), nullable=True),
        sa.Column("orientation", sa.String(length=10), nullable=True),
        sa.Column("gps_lat", sa.Double(), nullable=True),
        sa.Column("gps_lon", sa.Double(), nullable=True),
        sa.Column("gps_alt", sa.Double(), nullable=True),
        sa.Column("size_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary_short", sa.Text(), nullable=True),
        sa.Column("summary_long_de", sa.Text(), nullable=True),
        sa.Column("summary_long_en", sa.Text(), nullable=True),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "person_identifiable", sa.String(length=8), server_default="unknown", nullable=False
        ),
        sa.Column("consent", sa.String(length=8), server_default="unknown", nullable=False),
        sa.Column("consent_ref", sa.Text(), nullable=True),
        sa.Column("analysis_profile", sa.Text(), nullable=True),
        sa.Column("search_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("consent IN ('yes', 'no', 'unknown')", name=op.f("ck_asset_consent")),
        sa.CheckConstraint(
            "media_type IN ('video', 'photo', 'audio', 'other')", name=op.f("ck_asset_media_type")
        ),
        sa.CheckConstraint(
            "orientation IS NULL OR orientation IN ('landscape', 'portrait', 'square')",
            name=op.f("ck_asset_orientation"),
        ),
        sa.CheckConstraint(
            "person_identifiable IN ('yes', 'no', 'unknown')",
            name=op.f("ck_asset_person_identifiable"),
        ),
        sa.CheckConstraint(
            "status IN ('ingesting', 'processing', 'review', 'filed', 'quarantined', 'trashed', 'duplicate')",
            name=op.f("ck_asset_status"),
        ),
        sa.CheckConstraint(
            "rating IS NULL OR rating BETWEEN 0 AND 5", name=op.f("ck_asset_rating")
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batch.id"], name=op.f("fk_asset_batch_id_batch"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of"], ["asset.id"], name=op.f("fk_asset_duplicate_of_asset")
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"],
            ["filing_rule.id"],
            name=op.f("fk_asset_rule_version_id_filing_rule"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset")),
    )
    op.create_index("ix_asset_batch_id", "asset", ["batch_id"], unique=False, if_not_exists=True)
    op.create_index(
        "ix_asset_camera_model", "asset", ["camera_model"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_asset_media_type_capture_at",
        "asset",
        ["media_type", "capture_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_asset_search_tsv",
        "asset",
        ["search_tsv"],
        unique=False,
        postgresql_using="gin",
        if_not_exists=True,
    )
    op.create_index("ix_asset_status", "asset", ["status"], unique=False, if_not_exists=True)
    _create_table(
        "asset_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("original_rel_path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blake3", sa.Text(), nullable=True),
        sa.Column("quick_fp", sa.Text(), nullable=True),
        sa.Column("card_structure_root", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('primary', 'raw', 'jpeg', 'sidecar', 'proxy_camera', 'telemetry', 'audio_sync', 'member')",
            name=op.f("ck_asset_file_role"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.id"],
            name=op.f("fk_asset_file_asset_id_asset"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_file")),
        sa.UniqueConstraint("rel_path", name=op.f("uq_asset_file_rel_path")),
    )
    op.create_index(
        "ix_asset_file_asset_id", "asset_file", ["asset_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_asset_file_blake3", "asset_file", ["blake3"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_asset_file_quick_fp", "asset_file", ["quick_fp"], unique=False, if_not_exists=True
    )
    _create_table(
        "asset_metadata",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column(
            "normalized",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.id"],
            name=op.f("fk_asset_metadata_asset_id_asset"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("asset_id", name=op.f("pk_asset_metadata")),
    )
    _create_table(
        "input_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stable_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), server_default="copying", nullable=False),
        sa.Column("quick_fp", sa.Text(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "state IN ('copying', 'stable', 'grouped', 'ignored', 'gone')",
            name=op.f("ck_input_file_state"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.id"],
            name=op.f("fk_input_file_asset_id_asset"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batch.id"],
            name=op.f("fk_input_file_batch_id_batch"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_input_file")),
        sa.UniqueConstraint("rel_path", name=op.f("uq_input_file_rel_path")),
    )
    op.create_index(
        "ix_input_file_asset_id", "input_file", ["asset_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_input_file_batch_id_state",
        "input_file",
        ["batch_id", "state"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_input_file_quick_fp", "input_file", ["quick_fp"], unique=False, if_not_exists=True
    )
    _create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column(
            "depends_on",
            sa.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "enqueued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('probe', 'hash', 'preview', 'shots', 'audio', 'embed', 'vision', 'classify', 'finalize')",
            name=op.f("ck_job_stage"),
        ),
        sa.CheckConstraint(
            "state IN ('blocked', 'queued', 'leased', 'done', 'failed', 'cancelled')",
            name=op.f("ck_job_state"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["asset.id"], name=op.f("fk_job_asset_id_asset"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["lease_owner"],
            ["worker.id"],
            name=op.f("fk_job_lease_owner_worker"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
    )
    op.create_index("ix_job_asset_id", "job", ["asset_id"], unique=False, if_not_exists=True)
    op.create_index(
        "ix_job_lease_expires_at",
        "job",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("state = 'leased'"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_job_queue",
        "job",
        ["stage", sa.literal_column("priority DESC"), "enqueued_at"],
        unique=False,
        postgresql_where=sa.text("state = 'queued'"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_job_state_stage", "job", ["state", "stage"], unique=False, if_not_exists=True
    )
    _create_table(
        "move_journal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("from_rel", sa.Text(), nullable=False),
        sa.Column("to_rel", sa.Text(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.id"],
            name=op.f("fk_move_journal_asset_id_asset"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_move_journal")),
    )
    op.create_index(
        "ix_move_journal_asset_id", "move_journal", ["asset_id"], unique=False, if_not_exists=True
    )
    _create_table(
        "shot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column(
            "keyframes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("camera_motion", sa.Text(), nullable=True),
        sa.Column("sharpness", sa.Double(), nullable=True),
        sa.Column("shake", sa.Double(), nullable=True),
        sa.Column("exposure", sa.Double(), nullable=True),
        sa.Column("noise", sa.Double(), nullable=True),
        sa.Column("description_short", sa.Text(), nullable=True),
        sa.Column("description_long_de", sa.Text(), nullable=True),
        sa.Column("description_long_en", sa.Text(), nullable=True),
        sa.Column("shot_size", sa.Text(), nullable=True),
        sa.Column("angle", sa.Text(), nullable=True),
        sa.Column("search_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["asset.id"], name=op.f("fk_shot_asset_id_asset"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shot")),
        sa.UniqueConstraint("asset_id", "idx", name="uq_shot_asset_id_idx"),
    )
    op.create_index(
        "ix_shot_search_tsv",
        "shot",
        ["search_tsv"],
        unique=False,
        postgresql_using="gin",
        if_not_exists=True,
    )
    _create_table(
        "stage_result",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column(
            "output",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "stage IN ('probe', 'hash', 'preview', 'shots', 'audio', 'embed', 'vision', 'classify', 'finalize')",
            name=op.f("ck_stage_result_stage"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.id"],
            name=op.f("fk_stage_result_asset_id_asset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["job.id"], name=op.f("fk_stage_result_job_id_job"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["worker.id"],
            name=op.f("fk_stage_result_worker_id_worker"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stage_result")),
    )
    op.create_index(
        "ix_stage_result_asset_id_stage_finished_at",
        "stage_result",
        ["asset_id", "stage", sa.literal_column("finished_at DESC")],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ux_stage_result_job_id",
        "stage_result",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("job_id IS NOT NULL AND ok"),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Drop everything created by ``upgrade`` (indexes go with their tables)."""
    op.drop_table("stage_result", if_exists=True)
    op.drop_table("shot", if_exists=True)
    op.drop_table("move_journal", if_exists=True)
    op.drop_table("job", if_exists=True)
    op.drop_table("input_file", if_exists=True)
    op.drop_table("asset_metadata", if_exists=True)
    op.drop_table("asset_file", if_exists=True)
    op.drop_table("asset", if_exists=True)
    op.drop_table("session", if_exists=True)
    op.drop_table("filing_rule", if_exists=True)
    op.drop_table("enrollment_token", if_exists=True)
    op.drop_table("app_setting", if_exists=True)
    op.drop_table("api_token", if_exists=True)
    op.drop_table("worker", if_exists=True)
    op.drop_table("user", if_exists=True)
    op.drop_table("library", if_exists=True)
    op.drop_table("batch", if_exists=True)
    op.drop_table("audit_log", if_exists=True)
    op.execute("DROP EXTENSION IF EXISTS citext")
