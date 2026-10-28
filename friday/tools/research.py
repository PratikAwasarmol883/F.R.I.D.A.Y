"""Tools available to a research run: search whose results unlock fetching, plus fetch."""

from friday.adapters.fetch.base import PageFetcher
from friday.adapters.search.base import SearchProvider
from friday.core.config import Settings
from friday.orchestration.tool_registry import ToolRegistry
from friday.tools.web_fetch.ledger import SourceLedger
from friday.tools.web_fetch.tool import WebFetchTool
from friday.tools.web_search.schemas import WebSearchInput, WebSearchOutput
from friday.tools.web_search.tool import WebSearchTool


class LedgerSearchTool(WebSearchTool):
    """Search that records its result URLs as the only pages the model may fetch."""

    def __init__(self, ledger: SourceLedger, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._ledger = ledger

    async def run(self, arguments: WebSearchInput) -> WebSearchOutput:
        output = await super().run(arguments)
        self._ledger.allow_urls([item.url for item in output.results])
        return output


def build_research_registry(
    settings: Settings,
    ledger: SourceLedger,
    search_provider: SearchProvider,
    fetcher: PageFetcher,
    *,
    max_searches: int,
    max_fetches: int,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        LedgerSearchTool(
            ledger,
            search_provider,
            timeout_seconds=settings.tool_timeout_seconds,
            max_results=settings.search_max_results,
            max_calls_per_ask=max_searches,
            daily_limit=settings.search_daily_limit,
        )
    )
    registry.register(
        WebFetchTool(
            fetcher,
            ledger,
            timeout_seconds=settings.fetch_timeout_seconds,
            max_calls_per_ask=max_fetches,
            daily_limit=settings.fetch_daily_limit,
        )
    )
    return registry
