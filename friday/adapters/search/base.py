from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class SearchProviderError(Exception):
    """A search call failed. `code` is safe to store; `retryable` marks transient failures."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class SearchProvider(Protocol):
    async def search(
        self, query: str, max_results: int, timeout_seconds: float
    ) -> list[SearchHit]: ...
