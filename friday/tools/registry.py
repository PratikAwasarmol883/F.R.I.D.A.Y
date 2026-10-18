from friday.adapters.search.base import SearchProvider
from friday.adapters.search.tavily import TavilySearchProvider
from friday.core.config import Settings
from friday.orchestration.tool_registry import ToolRegistry
from friday.tools.web_search.tool import WebSearchTool


def build_registry(
    settings: Settings, search_provider: SearchProvider | None = None
) -> ToolRegistry:
    """Registers only tools whose providers are configured; an unconfigured tool does not exist."""
    registry = ToolRegistry()
    if search_provider is None and settings.search_key_configured and settings.search_api_key:
        search_provider = TavilySearchProvider(settings.search_api_key.get_secret_value())
    if search_provider is not None:
        registry.register(
            WebSearchTool(
                search_provider,
                timeout_seconds=settings.tool_timeout_seconds,
                max_results=settings.search_max_results,
                max_calls_per_ask=settings.max_tool_calls_per_ask,
                daily_limit=settings.search_daily_limit,
            )
        )
    return registry
