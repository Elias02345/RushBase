from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.mark.postgres
def test_initial_migration_enables_extensions_idempotently(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setenv("RUSHBASE_SECRET_KEY", "m" * 40)
    monkeypatch.setenv("RUSHBASE_DATABASE_URL", database_url)
    config = Config(str(ALEMBIC_INI))
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # re-running must be a no-op
    engine = create_engine(database_url)
    with engine.connect() as connection:
        names = set(connection.execute(text("SELECT extname FROM pg_extension")).scalars())
    engine.dispose()
    assert {"vector", "pg_trgm"} <= names
