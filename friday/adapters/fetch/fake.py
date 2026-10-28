from datetime import UTC, datetime

from friday.adapters.fetch.base import FetchedPage, FetchError


class FakeFetcher:
    """Serves canned pages (or errors) by exact URL; records every request."""

    def __init__(self, pages: dict[str, FetchedPage | FetchError] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[tuple[str, float]] = []

    def add_html(self, url: str, html: str, final_url: str | None = None) -> None:
        self.pages[url] = FetchedPage(
            url=final_url or url,
            status=200,
            content_type="text/html",
            text=html,
            size_bytes=len(html.encode()),
            retrieved_at=datetime.now(UTC),
        )

    async def fetch(self, url: str, timeout_seconds: float) -> FetchedPage:
        self.calls.append((url, timeout_seconds))
        result = self.pages.get(url)
        if result is None:
            raise FetchError("http_error")
        if isinstance(result, FetchError):
            raise result
        return result
