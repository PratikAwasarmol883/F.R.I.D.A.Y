"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from friday import __version__
from friday.api.error_handlers import register_error_handlers
from friday.api.routes import api_router
from friday.core.config import Settings, settings
from friday.core.logging_config import get_logger, setup_logging
from friday.core.request_context import BodyLimitMiddleware, RequestContextMiddleware
from friday.persistence.database import get_engine

logger = get_logger(__name__)

VOICE_PATH = "/api/v1/assistant/voice"
VOICE_FRAMING_BYTES = 64 * 1024  # multipart boundaries and form fields


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not settings.anthropic_key_configured:
        logger.warning("ANTHROPIC_API_KEY is not set; /assistant/ask will return 503")
    logger.info("FRIDAY startup complete")
    yield
    await get_engine().dispose()
    logger.info("FRIDAY shutdown")


def create_app(app_settings: Settings | None = None) -> FastAPI:
    cfg = app_settings or settings
    setup_logging()
    app = FastAPI(
        title="FRIDAY",
        description="Personal AI Assistant with a cloud-hosted brain",
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
    )
    limits = {VOICE_PATH: cfg.voice_max_upload_bytes + VOICE_FRAMING_BYTES}
    app.add_middleware(BodyLimitMiddleware, path_limits=limits)  # innermost: wraps `receive`
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(
        RequestContextMiddleware,
        path_limits=limits,
    )
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
