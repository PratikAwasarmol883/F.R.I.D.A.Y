from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class FetchError(Exception):
    """A fetch failed. `code` is stable and safe to store; it never reveals network internals."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FetchedPage:
    url: str  # final URL after redirects
    status: int
    content_type: str
    text: str
    size_bytes: int
    retrieved_at: datetime


class PageFetcher(Protocol):
    async def fetch(self, url: str, timeout_seconds: float) -> FetchedPage: ...
