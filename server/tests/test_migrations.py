import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from rushbase_server.models import Base
from support import ALEMBIC_INI, migrate


def _config(monkeypatch: pytest.MonkeyPatch, url: str) -> Config:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "m" * 40)
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", url)
    return Config(str(ALEMBIC_INI))


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_initial_migration_enables_extensions_idempotently(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    config = _config(monkeypatch, fresh_database_url)
    command.upgrade(config, "0001")
    command.upgrade(config, "0001")  # re-running must be a no-op
    engine = create_engine(fresh_database_url)
    with engine.connect() as connection:
        names = set(connection.execute(text("SELECT extname FROM pg_extension")).scalars())
    engine.dispose()
    assert {"vector", "pg_trgm"} <= names


@pytest.mark.postgres
def test_m1_migration_up_down_up(monkeypatch: pytest.MonkeyPatch, fresh_database_url: str) -> None:
    config = _config(monkeypatch, fresh_database_url)
    expected = {t.name for t in Base.metadata.sorted_tables}
    command.upgrade(config, "head")
    assert expected <= _tables(fresh_database_url)
    command.downgrade(config, "0001")
    assert not (expected & _tables(fresh_database_url))
    command.upgrade(config, "head")
    assert expected <= _tables(fresh_database_url)


@pytest.mark.postgres
def test_m1_migration_is_idempotent_after_partial_run(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    config = _config(monkeypatch, fresh_database_url)
    command.upgrade(config, "head")
    command.stamp(config, "0001")  # pretend the previous run died before recording the revision
    command.upgrade(config, "head")  # every object already exists: must not fail
    assert {t.name for t in Base.metadata.sorted_tables} <= _tables(fresh_database_url)


@pytest.mark.postgres
def test_models_match_the_migrations(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    _config(monkeypatch, fresh_database_url)
    migrate(fresh_database_url)
    engine = create_engine(fresh_database_url)
    try:
        with engine.connect() as connection:
            from alembic.autogenerate import compare_metadata
            from alembic.migration import MigrationContext

            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def _asset_columns(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns("asset")}
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_m2_migration_is_additive_idempotent_and_reversible(
    monkeypatch: pytest.MonkeyPatch, fresh_database_url: str
) -> None:
    config = _config(monkeypatch, fresh_database_url)
    new = {"decode_status", "derived", "perceptual_hash", "tech_summary_de", "quarantine"}
    command.upgrade(config, "0002")
    assert not new & _asset_columns(fresh_database_url)
    command.upgrade(config, "0003")
    assert new <= _asset_columns(fresh_database_url)
    command.stamp(config, "0002")
    command.upgrade(config, "0003")  # guards: every object already exists
    command.downgrade(config, "0002")
    assert not new & _asset_columns(fresh_database_url)
    command.upgrade(config, "head")
    assert new <= _asset_columns(fresh_database_url)


@pytest.mark.postgres
def test_queue_indexes_exist(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE tablename = 'job'")
            ).scalars()
            definitions = list(rows)
    finally:
        engine.dispose()
    assert any("'queued'" in d and "priority DESC" in d for d in definitions)
    assert any("'leased'" in d and "lease_expires_at" in d for d in definitions)
