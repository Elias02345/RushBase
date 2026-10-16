"""Runtime settings stored in the ``app_setting`` table."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from rushbase_server.models import AppSetting

QUEUE_PAUSED_KEY = "queue.paused"
PIPELINE_STAGES_KEY = "pipeline.stages"
PATH_ROOTS_KEY = "library.path_roots"
CENTRAL_META_KEY = "filing.central_meta"
OAUTH_EXTRA_REDIRECTS_KEY = "oauth.extra_redirects"


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    """Return the JSON value stored for ``key`` or ``default``."""
    row = await session.get(AppSetting, key)
    return default if row is None else row.value


async def set_setting(
    session: AsyncSession, key: str, value: Any, updated_by: UUID | None = None
) -> None:
    """Insert or replace the value of ``key`` (committed with the caller's transaction)."""
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value, updated_by=updated_by))
    else:
        row.value = value
        row.updated_by = updated_by
