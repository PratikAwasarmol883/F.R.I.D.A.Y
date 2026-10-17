"""API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""

    status: str  # "healthy" or "degraded"
    database: str  # "connected" or error message
    timestamp: datetime
    version: str
