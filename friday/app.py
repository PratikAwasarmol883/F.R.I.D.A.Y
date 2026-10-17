"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from friday import __version__
from friday.api.routes import router as api_router
from friday.core.config import settings
from friday.core.logging_config import get_logger, setup_logging
from friday.persistence.database import get_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("FRIDAY startup complete")
    yield
    await get_engine().dispose()
    logger.info("FRIDAY shutdown")


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="FRIDAY",
        description="Personal AI Assistant with a cloud-hosted brain",
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(api_router)
    return app


app = create_app()
