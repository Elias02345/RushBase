"""Database access (SQLAlchemy 2 async, psycopg 3)."""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine; connections are opened lazily."""
    timeout_ms = int(settings.statement_timeout_seconds * 1000)
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        connect_args={"options": f"-c statement_timeout={timeout_ms}"} if timeout_ms else {},
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Return a factory of sessions that keep loaded attributes after ``commit``."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def database_reachable(engine: AsyncEngine) -> bool:
    """Return whether a trivial query succeeds. Never raises."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database health check failed", exc_info=True)
        return False
    return True


ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


async def upgrade_to_head(settings: Settings) -> None:
    """Apply all migrations (``alembic upgrade head``) to the configured database."""
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = settings.database_url.get_secret_value()
    await asyncio.to_thread(command.upgrade, config, "head")
