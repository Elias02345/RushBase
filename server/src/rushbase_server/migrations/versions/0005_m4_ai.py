"""M4: taxonomy, tags, transcripts, audio events, AI description columns, embeddings, eval.

Additive and idempotent (``if_not_exists`` / ``ADD COLUMN IF NOT EXISTS`` / ``CREATE OR REPLACE``).
The full-text vector of assets now also covers the AI summaries, the transcript and the labels of
approved tags; transcript and tag rows refresh it through statement-level triggers (one refresh per
statement, not per row). ``downgrade`` removes everything created here and restores the 0004
functions; the ``vector`` extension stays installed.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SPLIT = "regexp_replace(coalesce({0}, ''), '[^[:alnum:]]+', ' ', 'g')"

# (table, column definition) pairs added to existing tables.
_REVIEW_STATES = "'none', 'pending', 'done'"
_TRI = "'yes', 'no', 'unknown'"
_ASSET_COLUMNS: tuple[tuple[str, str], ...] = (
    ("summary_short_en", "text"),
    ("shot_size", "text"),
    ("camera_angle", "text"),
    ("interior_exterior", "text"),
    ("time_of_day", "text"),
    ("people_count", "integer"),
    ("ai_details", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
    ("audio_info", "jsonb"),
    ("review_state", "text NOT NULL DEFAULT 'none'"),
    ("review_reasons", "text[] NOT NULL DEFAULT '{}'"),
    ("human_fields", "text[] NOT NULL DEFAULT '{}'"),
)
_SHOT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("description_short_de", "text"),
    ("description_short_en", "text"),
    ("interior_exterior", "text"),
    ("time_of_day", "text"),
    ("people_count", "integer"),
    ("person_identifiable", "text NOT NULL DEFAULT 'unknown'"),
    ("ai_details", "jsonb NOT NULL DEFAULT '{}'::jsonb"),
    ("human_fields", "text[] NOT NULL DEFAULT '{}'"),
)

_ASSET_FUNCTION = f"""
CREATE OR REPLACE FUNCTION asset_search_vector(a asset) RETURNS tsvector
LANGUAGE plpgsql STABLE AS $$
DECLARE
    primary_path text;
    all_paths text;
    batch_label text;
    transcript text;
    tag_labels text;
BEGIN
    SELECT rel_path INTO primary_path FROM asset_file WHERE id = a.primary_file_id;
    SELECT string_agg(rel_path, ' ') INTO all_paths FROM asset_file WHERE asset_id = a.id;
    SELECT label INTO batch_label FROM batch WHERE id = a.batch_id;
    SELECT left(string_agg(text, ' ' ORDER BY idx), 100000) INTO transcript
        FROM transcript_segment WHERE asset_id = a.id;
    SELECT string_agg(DISTINCT coalesce(v.label_de, '') || ' ' || coalesce(v.label_en, '') || ' '
               || v.value || ' ' || array_to_string(v.synonyms, ' '), ' ') INTO tag_labels
        FROM asset_tag t JOIN tax_value v ON v.id = t.value_id
        WHERE t.asset_id = a.id AND v.status = 'approved';
    RETURN
        setweight(to_tsvector('rushbase_simple', coalesce(a.title, '')), 'A')
        || setweight(to_tsvector('rushbase_simple',
               {_SPLIT.format("regexp_replace(primary_path, '^.*/', '')")}), 'A')
        || setweight(to_tsvector('rushbase_simple', {_SPLIT.format("batch_label")}), 'A')
        || setweight(to_tsvector('rushbase_simple',
               coalesce(a.summary_short, '') || ' ' || coalesce(a.summary_short_en, '') || ' '
               || coalesce(a.summary_long_de, '') || ' '
               || coalesce(a.summary_long_en, '') || ' ' || coalesce(a.tech_summary_de, '') || ' '
               || coalesce(a.tech_summary_en, '')), 'B')
        || setweight(to_tsvector('rushbase_simple', coalesce(tag_labels, '')), 'B')
        || setweight(to_tsvector('rushbase_simple',
               coalesce(a.camera_make, '') || ' ' || coalesce(a.camera_model, '') || ' '
               || coalesce(a.lens_model, '') || ' ' || coalesce(a.log_profile, '') || ' '
               || coalesce(a.codec, '')), 'C')
        || setweight(to_tsvector('rushbase_simple', coalesce(transcript, '')), 'C')
        || setweight(to_tsvector('rushbase_simple',
               {_SPLIT.format("all_paths || ' ' || coalesce(a.content_rel_dir, '')")}), 'D');
END
$$;
"""  # noqa: S608 - only module constants are interpolated

_SHOT_FUNCTION = """
CREATE OR REPLACE FUNCTION shot_search_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('rushbase_simple', coalesce(NEW.description_short, '') || ' '
            || coalesce(NEW.description_short_de, '') || ' '
            || coalesce(NEW.description_short_en, '')), 'A')
        || setweight(to_tsvector('rushbase_simple',
               coalesce(NEW.description_long_de, '') || ' ' || coalesce(NEW.description_long_en, '')),
           'B')
        || setweight(to_tsvector('rushbase_simple',
               coalesce(NEW.camera_motion, '') || ' ' || coalesce(NEW.shot_size, '') || ' '
               || coalesce(NEW.angle, '')), 'C');
    RETURN NEW;
END
$$;
"""

_REFRESH_FUNCTION = """
CREATE OR REPLACE FUNCTION asset_refresh_from_children() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE asset a SET search_tsv = asset_search_vector(a)
    WHERE a.id IN (SELECT DISTINCT asset_id FROM changed_rows);
    RETURN NULL;
END
$$;
"""

_ASSET_TRIGGER_COLUMNS = (
    "title, summary_short, summary_short_en, summary_long_de, summary_long_en, tech_summary_de, "
    "tech_summary_en, camera_make, camera_model, lens_model, log_profile, codec, content_rel_dir, "
    "primary_file_id, batch_id"
)
_SHOT_TRIGGER_COLUMNS = (
    "description_short, description_short_de, description_short_en, description_long_de, "
    "description_long_en, camera_motion, shot_size, angle"
)

# Statement-level triggers with transition tables (one asset refresh per statement).
_CHILD_TRIGGERS: tuple[tuple[str, str, str, str], ...] = (
    ("transcript_segment_tsv_ins", "transcript_segment", "INSERT", "NEW"),
    ("transcript_segment_tsv_del", "transcript_segment", "DELETE", "OLD"),
    ("asset_tag_tsv_ins", "asset_tag", "INSERT", "NEW"),
    ("asset_tag_tsv_del", "asset_tag", "DELETE", "OLD"),
)


def _load_0004() -> object:
    path = Path(__file__).with_name("0004_m3_search.py")
    spec = importlib.util.spec_from_file_location("rushbase_migration_0004", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _uuid_pk(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.Uuid(), primary_key=True)


def _now(name: str = "created_at") -> sa.Column[Any]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _tables() -> None:
    op.create_table(
        "tax_field",
        _uuid_pk("id"),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("label_de", sa.Text(), nullable=False),
        sa.Column("label_en", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="single"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        _now(),
        sa.CheckConstraint("kind IN ('single', 'multi')", name="kind"),
        if_not_exists=True,
    )
    op.create_table(
        "tax_value",
        _uuid_pk("id"),
        sa.Column(
            "field_id", sa.Uuid(), sa.ForeignKey("tax_field.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("label_de", sa.Text()),
        sa.Column("label_en", sa.Text()),
        sa.Column("synonyms", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("tax_value.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "example_asset_ids", postgresql.ARRAY(sa.Uuid()), nullable=False, server_default="{}"
        ),
        _now(),
        _now("updated_at"),
        sa.CheckConstraint("status IN ('proposed', 'approved', 'deprecated')", name="status"),
        if_not_exists=True,
    )
    op.create_index(
        "ux_tax_value_field_value",
        "tax_value",
        ["field_id", sa.text("lower(value)")],
        unique=True,
        if_not_exists=True,
    )
    op.create_table(
        "taxonomy_version",
        sa.Column("id", sa.SmallInteger(), primary_key=True, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _now("updated_at"),
        sa.CheckConstraint("id = 1", name="single"),
        if_not_exists=True,
    )
    op.create_table(
        "asset_tag",
        _uuid_pk("id"),
        sa.Column(
            "asset_id", sa.Uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("shot_id", sa.Uuid(), sa.ForeignKey("shot.id", ondelete="CASCADE")),
        sa.Column(
            "field_id", sa.Uuid(), sa.ForeignKey("tax_field.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "value_id", sa.Uuid(), sa.ForeignKey("tax_value.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("model_ref", postgresql.JSONB(), nullable=False, server_default="{}"),
        _now(),
        sa.CheckConstraint("source IN ('ai', 'claude', 'human')", name="source"),
        if_not_exists=True,
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_asset_tag_unique ON asset_tag "
        "(asset_id, shot_id, value_id, source) NULLS NOT DISTINCT"
    )
    op.create_index("ix_asset_tag_value_id", "asset_tag", ["value_id"], if_not_exists=True)
    op.create_index("ix_asset_tag_asset_id", "asset_tag", ["asset_id"], if_not_exists=True)
    op.create_table(
        "transcript_segment",
        _uuid_pk("id"),
        sa.Column(
            "asset_id", sa.Uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text()),
        sa.Column("speaker", sa.Text()),
        sa.Column("words", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('rushbase_simple'::regconfig, text)", persisted=True),
        ),
        sa.UniqueConstraint("asset_id", "idx", name="uq_transcript_segment_asset_id_idx"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_transcript_segment_tsv",
        "transcript_segment",
        ["tsv"],
        postgresql_using="gin",
        if_not_exists=True,
    )
    op.create_table(
        "audio_event",
        _uuid_pk("id"),
        sa.Column(
            "asset_id", sa.Uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("raw_top", postgresql.JSONB(), nullable=False, server_default="[]"),
        if_not_exists=True,
    )
    op.create_index("ix_audio_event_asset_id", "audio_event", ["asset_id"], if_not_exists=True)
    op.create_index("ix_audio_event_tag", "audio_event", ["tag"], if_not_exists=True)
    op.execute(
        "CREATE TABLE IF NOT EXISTS embedding_image ("
        "id uuid PRIMARY KEY, "
        "asset_id uuid NOT NULL REFERENCES asset(id) ON DELETE CASCADE, "
        "shot_id uuid REFERENCES shot(id) ON DELETE CASCADE, "
        "keyframe text NOT NULL, model_id text NOT NULL, revision text NOT NULL, "
        "embedding vector(768) NOT NULL, created_at timestamptz NOT NULL DEFAULT now())"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS embedding_text ("
        "id uuid PRIMARY KEY, "
        "asset_id uuid NOT NULL REFERENCES asset(id) ON DELETE CASCADE, "
        "shot_id uuid REFERENCES shot(id) ON DELETE CASCADE, "
        "kind text NOT NULL, model_id text NOT NULL, revision text NOT NULL, "
        "start_ms bigint, end_ms bigint, language text, "
        "embedding vector(768) NOT NULL, created_at timestamptz NOT NULL DEFAULT now())"
    )
    for table in ("embedding_image", "embedding_text"):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_hnsw ON {table} "
            "USING hnsw (embedding vector_cosine_ops)"
        )
        op.create_index(f"ix_{table}_asset_id", table, ["asset_id"], if_not_exists=True)
        op.create_index(f"ix_{table}_shot_id", table, ["shot_id"], if_not_exists=True)
    op.create_table(
        "golden_label",
        _uuid_pk("id"),
        sa.Column(
            "asset_id", sa.Uuid(), sa.ForeignKey("asset.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("shot_id", sa.Uuid(), sa.ForeignKey("shot.id", ondelete="CASCADE")),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("labeller_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("origin", sa.Text(), nullable=False, server_default="label"),
        _now(),
        sa.CheckConstraint("origin IN ('label', 'correction')", name="origin"),
        if_not_exists=True,
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_golden_label_unique ON golden_label "
        "(asset_id, shot_id, field) NULLS NOT DISTINCT"
    )
    op.create_table(
        "eval_run",
        _uuid_pk("id"),
        _now(),
        sa.Column("label", sa.Text()),
        sa.Column("model_refs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("golden_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        if_not_exists=True,
    )


def upgrade() -> None:
    """Create the M4 tables and columns and extend the search vectors."""
    for table, columns in (("asset", _ASSET_COLUMNS), ("shot", _SHOT_COLUMNS)):
        for name, definition in columns:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition}")
    op.execute(
        "ALTER TABLE asset DROP CONSTRAINT IF EXISTS ck_asset_review_state, "
        f"ADD CONSTRAINT ck_asset_review_state CHECK (review_state IN ({_REVIEW_STATES}))"
    )
    op.execute(
        "ALTER TABLE shot DROP CONSTRAINT IF EXISTS ck_shot_person_identifiable, "
        f"ADD CONSTRAINT ck_shot_person_identifiable CHECK (person_identifiable IN ({_TRI}))"
    )
    op.create_index(
        "ix_asset_review_state",
        "asset",
        ["review_state"],
        if_not_exists=True,
        postgresql_where=sa.text("review_state <> 'none'"),
    )
    _tables()
    op.execute(_ASSET_FUNCTION)
    op.execute(_SHOT_FUNCTION)
    op.execute(_REFRESH_FUNCTION)
    op.execute("DROP TRIGGER IF EXISTS asset_search_tsv ON asset")
    op.execute(
        f"CREATE TRIGGER asset_search_tsv BEFORE INSERT OR UPDATE OF {_ASSET_TRIGGER_COLUMNS} "
        "ON asset FOR EACH ROW EXECUTE FUNCTION asset_search_trigger()"
    )
    op.execute("DROP TRIGGER IF EXISTS shot_search_tsv ON shot")
    op.execute(
        f"CREATE TRIGGER shot_search_tsv BEFORE INSERT OR UPDATE OF {_SHOT_TRIGGER_COLUMNS} "
        "ON shot FOR EACH ROW EXECUTE FUNCTION shot_search_trigger()"
    )
    for name, table, event, transition in _CHILD_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        op.execute(
            f"CREATE TRIGGER {name} AFTER {event} ON {table} "
            f"REFERENCING {transition} TABLE AS changed_rows "
            "FOR EACH STATEMENT EXECUTE FUNCTION asset_refresh_from_children()"
        )
    op.execute("UPDATE asset a SET search_tsv = asset_search_vector(a)")
    op.execute("UPDATE shot SET description_short = description_short")


def downgrade() -> None:
    """Drop the M4 objects and restore the 0004 search functions and triggers."""
    for name, table, _event, _transition in _CHILD_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS asset_refresh_from_children()")
    for table in (
        "eval_run",
        "golden_label",
        "embedding_text",
        "embedding_image",
        "audio_event",
        "transcript_segment",
        "asset_tag",
        "taxonomy_version",
        "tax_value",
        "tax_field",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    old = _load_0004()
    op.execute("DROP TRIGGER IF EXISTS asset_search_tsv ON asset")
    op.execute("DROP TRIGGER IF EXISTS shot_search_tsv ON shot")
    op.execute(old._ASSET_FUNCTION)  # type: ignore[attr-defined]
    op.execute(old._SHOT_FUNCTION)  # type: ignore[attr-defined]
    op.execute(
        f"CREATE TRIGGER asset_search_tsv BEFORE INSERT OR UPDATE OF {old._ASSET_COLUMNS} "  # type: ignore[attr-defined]
        "ON asset FOR EACH ROW EXECUTE FUNCTION asset_search_trigger()"
    )
    op.execute(
        f"CREATE TRIGGER shot_search_tsv BEFORE INSERT OR UPDATE OF {old._SHOT_COLUMNS} "  # type: ignore[attr-defined]
        "ON shot FOR EACH ROW EXECUTE FUNCTION shot_search_trigger()"
    )
    op.execute("DROP INDEX IF EXISTS ix_asset_review_state")
    for table, columns in (("shot", _SHOT_COLUMNS), ("asset", _ASSET_COLUMNS)):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_review_state")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_person_identifiable")
        for name, _definition in columns:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {name}")
    op.execute("UPDATE asset a SET search_tsv = asset_search_vector(a)")
    op.execute("UPDATE shot SET description_short = description_short")
