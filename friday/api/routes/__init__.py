from fastapi import APIRouter

from friday.api.routes import assistant, conversations, health

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(conversations.router)
api_router.include_router(assistant.router)
