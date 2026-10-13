"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel

from rushbase_server import get_version
from rushbase_server.db import create_engine, database_reachable
from rushbase_server.settings import Settings


class HealthResponse(BaseModel):
    """Result of the health probe."""

    status: Literal["ok", "degraded"]
    version: str
    database: bool


def create_app(settings: Settings) -> FastAPI:
    """Build the application for the given settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Rushbase", version=get_version(), lifespan=lifespan)

    @app.get(
        "/health",
        response_model=HealthResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
        tags=["system"],
        summary="Liveness and database reachability",
    )
    async def health(request: Request, response: Response) -> HealthResponse:
        database = await database_reachable(request.app.state.engine)
        if not database:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="ok" if database else "degraded", version=get_version(), database=database
        )

    return app
