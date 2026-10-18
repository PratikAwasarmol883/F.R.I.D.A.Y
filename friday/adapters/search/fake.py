import asyncio

from friday.adapters.search.base import SearchHit, SearchProviderError


class FakeSearchProvider:
    """Deterministic search provider for tests; records calls and can fail or stall."""

    def __init__(
        self,
        hits: list[SearchHit] | None = None,
        error: SearchProviderError | None = None,
        delay_seconds: float = 0.0,
        fail_first: int = 0,
    ) -> None:
        self.hits = (
            hits
            if hits is not None
            else [SearchHit("Example", "https://example.com", "An example.")]
        )
        self.error = error
        self.delay_seconds = delay_seconds
        self.fail_first = fail_first
        self.calls: list[tuple[str, int, float]] = []

    async def search(self, query: str, max_results: int, timeout_seconds: float) -> list[SearchHit]:
        self.calls.append((query, max_results, timeout_seconds))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        if len(self.calls) <= self.fail_first:
            raise SearchProviderError("upstream_error", retryable=True)
        return list(self.hits)  # deliberately ignores max_results to test the tool's own cap
