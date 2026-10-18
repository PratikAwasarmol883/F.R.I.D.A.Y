from friday.adapters.search.base import SearchProvider, SearchProviderError
from friday.core.redaction import contains_secret
from friday.domain.enums import RiskClass
from friday.orchestration.sanitize import clean_text, sanitize_url
from friday.orchestration.tool_contracts import Source, Tool, ToolContract, ToolError, ToolSummary
from friday.tools.web_search.schemas import (
    SearchResultItem,
    WebSearchInput,
    WebSearchOutput,
)

_TITLE_CHARS = 200
_SNIPPET_CHARS = 500


class WebSearchTool(Tool[WebSearchInput, WebSearchOutput]):
    def __init__(
        self,
        provider: SearchProvider,
        *,
        timeout_seconds: float,
        max_results: int,
        max_calls_per_ask: int,
        daily_limit: int,
    ) -> None:
        self._provider = provider
        self._max_results = max_results
        self.contract = ToolContract(
            name="web_search",
            version="1",
            description=(
                "Search the web and return up to a few results (title, URL, short snippet). "
                "Use plain-text queries only; do not include URLs or secrets."
            ),
            input_model=WebSearchInput,
            output_model=WebSearchOutput,
            risk_class=RiskClass.READ_ONLY,
            required_scope="tool:web_search",
            timeout_seconds=timeout_seconds,
            max_retries=1,
            returns_untrusted_content=True,
            max_calls_per_ask=max_calls_per_ask,
            daily_limit=daily_limit,
        )

    def guard(self, arguments: WebSearchInput) -> str | None:
        """Keep the outbound query plain text so it cannot smuggle private data to a third party."""
        query = arguments.query
        if not query.strip():
            return "empty_query"
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in query):
            return "control_characters"
        if "://" in query:
            return "url_in_query"
        if contains_secret(query):
            return "secret_in_query"
        return None

    def summarize(self, output: WebSearchOutput) -> ToolSummary:
        return ToolSummary(
            meta={"count": len(output.results), "urls": [r.url for r in output.results]},
            sources=tuple(Source(r.title or r.url, r.url) for r in output.results),
        )

    async def run(self, arguments: WebSearchInput) -> WebSearchOutput:
        limit = min(arguments.max_results, self._max_results)
        try:
            hits = await self._provider.search(
                arguments.query.strip(), limit, self.contract.timeout_seconds
            )
        except SearchProviderError as exc:
            raise ToolError(exc.code, retryable=exc.retryable) from exc

        results: list[SearchResultItem] = []
        for hit in hits:
            url = sanitize_url(hit.url)
            if url is None:
                continue
            results.append(
                SearchResultItem(
                    title=clean_text(hit.title, _TITLE_CHARS),
                    url=url,
                    snippet=clean_text(hit.snippet, _SNIPPET_CHARS),
                )
            )
            if len(results) >= limit:
                break
        return WebSearchOutput(results=results)
