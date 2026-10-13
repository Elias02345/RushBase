"""Alembic environment: online migrations against ``RUSHBASE_DATABASE_URL``."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from rushbase_server.settings import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# No ORM models yet (M1 adds them); migrations are written by hand.
target_metadata = None


def run_migrations_online() -> None:
    """Run migrations with a short-lived synchronous psycopg connection."""
    settings = Settings()
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported; run against a database")
run_migrations_online()
