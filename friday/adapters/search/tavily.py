from typing import Any

import httpx

from friday.adapters.search.base import SearchHit, SearchProviderError

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESPONSE_BYTES = 1_000_000


class TavilySearchProvider:
    """Calls one fixed host; the URL and redirects are never influenced by model or user input."""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: str, max_results: int, timeout_seconds: float) -> list[SearchHit]:
        body = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,  # never forward a third party's synthesized answer
            "include_raw_content": False,
            "include_images": False,
        }
        try:
            response = await self._client.post(
                TAVILY_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise SearchProviderError("timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("network_error", retryable=True) from exc

        status = response.status_code
        if status == 429:
            raise SearchProviderError("rate_limited", retryable=True)
        if status in (432, 433):
            raise SearchProviderError("quota_exceeded")
        if status == 401:
            raise SearchProviderError("auth_error")
        if status >= 500:
            raise SearchProviderError("upstream_error", retryable=True)
        if status != 200:
            raise SearchProviderError("bad_request")
        return self._parse(response, max_results)

    @staticmethod
    def _parse(response: httpx.Response, max_results: int) -> list[SearchHit]:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SearchProviderError("bad_response")
        try:
            data: Any = response.json()
            items = data["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SearchProviderError("bad_response") from exc
        if not isinstance(items, list):
            raise SearchProviderError("bad_response")

        hits: list[SearchHit] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title, url, content = item.get("title"), item.get("url"), item.get("content")
            if not isinstance(url, str) or not url:
                continue
            hits.append(
                SearchHit(
                    title=title if isinstance(title, str) else "",
                    url=url,
                    snippet=content if isinstance(content, str) else "",
                )
            )
            if len(hits) >= max_results:
                break
        return hits
