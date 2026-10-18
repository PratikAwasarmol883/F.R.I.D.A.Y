"""API route handlers."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from friday import __version__
from friday.api.schemas import HealthResponse
from friday.core.logging_config import get_logger
from friday.persistence.database import get_db_session

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(session: AsyncSession = Depends(get_db_session)) -> HealthResponse:
    """Report service status and database connectivity without leaking error details."""
    database_status = "connected"
    status = "healthy"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check database probe failed")
        database_status = "unavailable"
        status = "degraded"

    return HealthResponse(
        status=status,
        database=database_status,
        timestamp=datetime.now(UTC),
        version=__version__,
    )
