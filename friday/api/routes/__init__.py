from fastapi import APIRouter

from friday.api.routes import (
    assistant,
    conversations,
    health,
    memory,
    reports,
    research,
    schedules,
    tasks,
    voice,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(conversations.router)
api_router.include_router(assistant.router)
api_router.include_router(tasks.router)
api_router.include_router(research.router)
api_router.include_router(reports.router)
api_router.include_router(voice.router)
api_router.include_router(memory.router)
api_router.include_router(schedules.router)
