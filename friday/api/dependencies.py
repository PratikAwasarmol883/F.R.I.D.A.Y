from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.llm import LLMProvider
from friday.composition import LazyLLMProvider, LazySpeech, get_registry
from friday.core.config import Settings, get_settings
from friday.core.logging_config import get_logger
from friday.orchestration.tool_registry import ToolRegistry
from friday.persistence.database import get_db_session
from friday.services.auth_service import AuthService, CurrentUser

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db_session),
) -> CurrentUser:
    token = credentials.credentials if credentials else None
    try:
        return await AuthService(session).authenticate(token)
    except Exception:
        logger.warning("Authentication failed")
        raise


def get_llm_provider(settings: Settings = Depends(get_settings)) -> LLMProvider:
    return LazyLLMProvider(settings)


def get_speech_provider(settings: Settings = Depends(get_settings)) -> LazySpeech:
    return LazySpeech(settings)


def get_tool_registry(settings: Settings = Depends(get_settings)) -> ToolRegistry:
    return get_registry(settings)
