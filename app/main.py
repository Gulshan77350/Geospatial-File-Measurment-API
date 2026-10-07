"""Application factory and ASGI entry point (``uvicorn app.main:app``)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers ORM tables on Base.metadata)
from app.api.files import router as files_router
from app.core.config import Settings, get_settings
from app.core.database import Base, build_engine, build_session_factory
from app.core.exceptions import GeoAPIError


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        engine = build_engine(settings.database_url)
        Base.metadata.create_all(engine)
        app.state.settings = settings
        app.state.session_factory = build_session_factory(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Upload a Shapefile (.zip) or KML file, extract its features and get area / length "
            "measurements computed in an appropriate projected CRS."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(GeoAPIError)
    async def _handle_domain_error(_request: Request, exc: GeoAPIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.get("/health", tags=["meta"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(files_router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
