"""Database access (SQLAlchemy 2 async, psycopg 3)."""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine; connections are opened lazily."""
    return create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)


async def database_reachable(engine: AsyncEngine) -> bool:
    """Return whether a trivial query succeeds. Never raises."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database health check failed", exc_info=True)
        return False
    return True
