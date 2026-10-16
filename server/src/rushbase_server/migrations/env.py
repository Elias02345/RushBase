"""Alembic environment: online migrations against ``RUSHBASE_DATABASE_URL``."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from rushbase_server.models import Base
from rushbase_server.settings import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Migrations are hand-reviewed; the metadata only feeds `alembic revision --autogenerate`.
target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Run migrations with a short-lived synchronous psycopg connection."""
    url = config.attributes.get("database_url") or Settings().database_url.get_secret_value()
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported; run against a database")
run_migrations_online()
