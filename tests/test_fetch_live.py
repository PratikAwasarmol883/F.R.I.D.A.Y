import asyncio
import os

import pytest

from friday.adapters.fetch.safe_fetcher import SafeFetcher
from friday.tools.web_fetch.extract import extract_text

pytestmark = pytest.mark.skipif(
    not os.environ.get("FRIDAY_LIVE_FETCH"),
    reason="set FRIDAY_LIVE_FETCH=1 on a machine with internet access (opt-in live test)",
)


@pytest.mark.parametrize("url", ["https://example.com/", "http://example.com/"])
def test_real_pages_are_fetched_through_the_pinned_connection(url: str) -> None:
    """Proves the pinned-IP request still validates TLS against the real hostname."""
    page = asyncio.run(SafeFetcher().fetch(url, 20))
    assert page.status == 200 and page.content_type == "text/html"
    assert "Example Domain" in extract_text(page.text, page.content_type, 2000).text
