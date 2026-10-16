"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from rushbase_core import LibraryMismatchError
from rushbase_server import get_version
from rushbase_server.api import (
    admin,
    auth,
    catalog,
    filing,
    review,
    search,
    system,
    taxonomy,
    worker,
)
from rushbase_server.clock import Clock, SystemClock
from rushbase_server.db import create_engine, create_session_factory
from rushbase_server.models import Library
from rushbase_server.runtime import Runtime
from rushbase_server.search.encoder import build_encoder
from rushbase_server.services.backup import PgTools
from rushbase_server.services.library_binding import LibraryBinding
from rushbase_server.services.ratelimit import LoginRateLimiter
from rushbase_server.settings import Settings

logger = logging.getLogger(__name__)


async def _bind_library(runtime: Runtime) -> None:
    """Open the library, take the server lease and verify it matches the database."""
    if not await runtime.binding.bind(force=runtime.settings.force_takeover):
        logger.info("no initialised library yet; waiting for first-run setup")
        return
    if runtime.settings.force_takeover:
        logger.warning("server lease taken over with --force-takeover")
    try:
        async with runtime.session_factory() as session:
            known = (await session.execute(select(Library.id))).scalars().first()
    except Exception:
        logger.warning("could not compare library id with the database", exc_info=True)
        return
    if known is not None and known != runtime.binding.library_id:
        await runtime.binding.release()
        raise LibraryMismatchError(
            f"database belongs to library {known}, but {runtime.binding.root} holds "
            f"{runtime.binding.library_id} (wrong mount?)"
        )


def create_app(settings: Settings, clock: Clock | None = None) -> FastAPI:
    """Build the application for the given settings (``clock`` is injectable for tests)."""
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        runtime = Runtime(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            clock=clock,
            binding=LibraryBinding(settings, clock),
            limiter=LoginRateLimiter(
                clock, settings.login_max_failures, settings.login_window_seconds
            ),
            pg_tools=PgTools.from_settings(settings),
            encoder=build_encoder(settings),
        )
        app.state.runtime = runtime
        try:
            await _bind_library(runtime)
            if settings.background_tasks:
                runtime.start()
            yield
        finally:
            await runtime.stop()
            await engine.dispose()

    app = FastAPI(title="Rushbase", version=get_version(), lifespan=lifespan)
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(worker.router)
    app.include_router(catalog.router)
    app.include_router(filing.router)
    app.include_router(taxonomy.router)
    app.include_router(search.router)
    app.include_router(review.router)
    return app
