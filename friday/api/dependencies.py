from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.llm import LLMProvider
from friday.adapters.llm.anthropic_provider import build_anthropic_provider
from friday.core.config import Settings, get_settings
from friday.core.logging_config import get_logger
from friday.domain.exceptions import ProviderUnavailableError
from friday.orchestration.tool_registry import ToolRegistry
from friday.persistence.database import get_db_session
from friday.services.auth_service import AuthService, CurrentUser
from friday.tools.registry import build_registry

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
    if not settings.anthropic_key_configured or settings.anthropic_api_key is None:
        logger.error("ANTHROPIC_API_KEY is not configured")
        raise ProviderUnavailableError
    return build_anthropic_provider(
        settings.anthropic_api_key.get_secret_value(), settings.llm_model
    )


_registry_cache: dict[tuple[object, ...], ToolRegistry] = {}


def get_tool_registry(settings: Settings = Depends(get_settings)) -> ToolRegistry:
    """One registry (and HTTP client) per distinct tool configuration."""
    key = (
        settings.search_provider,
        settings.search_api_key.get_secret_value() if settings.search_api_key else "",
        settings.tool_timeout_seconds,
        settings.search_max_results,
        settings.max_tool_calls_per_ask,
        settings.search_daily_limit,
    )
    if key not in _registry_cache:
        _registry_cache[key] = build_registry(settings)
    return _registry_cache[key]
