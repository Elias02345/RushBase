"""M3: full-text search (accent-insensitive configuration, weighted vectors, triggers, backfill).

Additive and idempotent: creates the ``unaccent`` extension, the text search configuration
``rushbase_simple`` and PL/pgSQL functions/triggers that keep ``asset.search_tsv`` and
``shot.search_tsv`` (columns and GIN indexes exist since ``0002``) up to date, then backfills all
existing rows. ``downgrade`` removes the triggers, functions and configuration and clears the
vectors; the ``unaccent`` extension is left installed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORD_TOKENS = (
    "asciiword, word, numword, asciihword, hword, numhword, hword_asciipart, hword_part, "
    "hword_numpart"
)

# Path-like text is split at every non-alphanumeric character so that ``IMG_1234.mov`` and
# ``Harbour/Clip-01`` yield searchable words instead of file/host tokens.
_SPLIT = "regexp_replace(coalesce({0}, ''), '[^[:alnum:]]+', ' ', 'g')"

_ASSET_FUNCTION = f"""
CREATE OR REPLACE FUNCTION asset_search_vector(a asset) RETURNS tsvector
LANGUAGE plpgsql STABLE AS $$
DECLARE
    primary_path text;
    all_paths text;
    batch_label text;
BEGIN
    SELECT rel_path INTO primary_path FROM asset_file WHERE id = a.primary_file_id;
    SELECT string_agg(rel_path, ' ') INTO all_paths FROM asset_file WHERE asset_id = a.id;
    SELECT label INTO batch_label FROM batch WHERE id = a.batch_id;
    RETURN
        setweight(to_tsvector('rushbase_simple', coalesce(a.title, '')), 'A')
        || setweight(to_tsvector('rushbase_simple',
               {_SPLIT.format("regexp_replace(primary_path, '^.*/', '')")}), 'A')
        || setweight(to_tsvector('rushbase_simple', {_SPLIT.format("batch_label")}), 'A')
        || setweight(to_tsvector('rushbase_simple',
               coalesce(a.summary_short, '') || ' ' || coalesce(a.summary_long_de, '') || ' '
               || coalesce(a.summary_long_en, '') || ' ' || coalesce(a.tech_summary_de, '') || ' '
               || coalesce(a.tech_summary_en, '')), 'B')
        || setweight(to_tsvector('rushbase_simple',
               coalesce(a.camera_make, '') || ' ' || coalesce(a.camera_model, '') || ' '
               || coalesce(a.lens_model, '') || ' ' || coalesce(a.log_profile, '') || ' '
               || coalesce(a.codec, '')), 'C')
        || setweight(to_tsvector('rushbase_simple',
               {_SPLIT.format("all_paths || ' ' || coalesce(a.content_rel_dir, '')")}), 'D');
END
$$;
"""  # noqa: S608 - only module constants are interpolated

_ASSET_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION asset_search_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv := asset_search_vector(NEW);
    RETURN NEW;
END
$$;
"""

_FILE_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION asset_file_search_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE asset a SET search_tsv = asset_search_vector(a)
    WHERE a.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.asset_id ELSE NEW.asset_id END;
    RETURN NULL;
END
$$;
"""

_BATCH_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION batch_search_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE asset a SET search_tsv = asset_search_vector(a) WHERE a.batch_id = NEW.id;
    RETURN NULL;
END
$$;
"""

_SHOT_FUNCTION = """
CREATE OR REPLACE FUNCTION shot_search_trigger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv :=
        setweight(to_tsvector('rushbase_simple', coalesce(NEW.description_short, '')), 'A')
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

_ASSET_COLUMNS = (
    "title, summary_short, summary_long_de, summary_long_en, tech_summary_de, tech_summary_en, "
    "camera_make, camera_model, lens_model, log_profile, codec, content_rel_dir, "
    "primary_file_id, batch_id"
)
_SHOT_COLUMNS = (
    "description_short, description_long_de, description_long_en, camera_motion, shot_size, angle"
)

# (trigger name, table, CREATE TRIGGER statement tail)
_TRIGGERS: tuple[tuple[str, str, str], ...] = (
    (
        "asset_search_tsv",
        "asset",
        f"BEFORE INSERT OR UPDATE OF {_ASSET_COLUMNS} ON asset "
        "FOR EACH ROW EXECUTE FUNCTION asset_search_trigger()",
    ),
    (
        "asset_file_search_tsv",
        "asset_file",
        "AFTER INSERT OR DELETE OR UPDATE OF rel_path, asset_id ON asset_file "
        "FOR EACH ROW EXECUTE FUNCTION asset_file_search_trigger()",
    ),
    (
        "batch_search_tsv",
        "batch",
        "AFTER UPDATE OF label ON batch FOR EACH ROW EXECUTE FUNCTION batch_search_trigger()",
    ),
    (
        "shot_search_tsv",
        "shot",
        f"BEFORE INSERT OR UPDATE OF {_SHOT_COLUMNS} ON shot "
        "FOR EACH ROW EXECUTE FUNCTION shot_search_trigger()",
    ),
)

_CONFIG = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'rushbase_simple') THEN
        CREATE TEXT SEARCH CONFIGURATION rushbase_simple (COPY = simple);
        ALTER TEXT SEARCH CONFIGURATION rushbase_simple
            ALTER MAPPING FOR {_WORD_TOKENS} WITH unaccent, simple;
    END IF;
END
$$;
"""  # noqa: S608 - only module constants are interpolated


def upgrade() -> None:
    """Create the configuration, functions and triggers and backfill existing rows."""
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(_CONFIG)
    for statement in (
        _ASSET_FUNCTION,
        _ASSET_TRIGGER_FUNCTION,
        _FILE_TRIGGER_FUNCTION,
        _BATCH_TRIGGER_FUNCTION,
        _SHOT_FUNCTION,
    ):
        op.execute(statement)
    for name, table, tail in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        op.execute(f"CREATE TRIGGER {name} {tail}")
    op.execute("UPDATE asset a SET search_tsv = asset_search_vector(a)")
    op.execute("UPDATE shot SET description_short = description_short")


def downgrade() -> None:
    """Remove the triggers, functions and configuration and clear the vectors."""
    for name, table, _tail in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for function in (
        "shot_search_trigger()",
        "batch_search_trigger()",
        "asset_file_search_trigger()",
        "asset_search_trigger()",
        "asset_search_vector(asset)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute("UPDATE asset SET search_tsv = NULL")
    op.execute("UPDATE shot SET search_tsv = NULL")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS rushbase_simple")
