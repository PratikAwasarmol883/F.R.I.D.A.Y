import asyncio
import json

import httpx
import pytest

from friday.adapters.search.base import SearchProviderError
from friday.adapters.search.tavily import MAX_RESPONSE_BYTES, TAVILY_URL, TavilySearchProvider

KEY = "tvly-test-secret-key-123456"


def _provider(handler: httpx.MockTransport) -> TavilySearchProvider:
    return TavilySearchProvider(KEY, client=httpx.AsyncClient(transport=handler))


def _search(handler: httpx.MockTransport, max_results: int = 5):  # type: ignore[no-untyped-def]
    return asyncio.run(_provider(handler).search("python docs", max_results, 3.0))


def _ok(payload: object, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=payload))


def test_request_shape_is_fixed_and_authenticated() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": []})

    _search(httpx.MockTransport(handler), max_results=4)
    assert seen["url"] == TAVILY_URL
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"] == {
        "query": "python docs",
        "max_results": 4,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }


def test_results_are_parsed_and_capped_and_malformed_items_skipped() -> None:
    payload = {
        "answer": "SHOULD BE IGNORED",
        "results": [
            {"title": "A", "url": "https://a.example", "content": "alpha", "score": 0.9},
            "not-a-dict",
            {"title": "no url", "content": "x"},
            {"title": 5, "url": "https://b.example", "content": None},
            {"title": "C", "url": "https://c.example", "content": "gamma"},
        ],
    }
    hits = _search(_ok(payload), max_results=2)
    assert [(h.title, h.url, h.snippet) for h in hits] == [
        ("A", "https://a.example", "alpha"),
        ("", "https://b.example", ""),
    ]


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (429, "rate_limited", True),
        (432, "quota_exceeded", False),
        (433, "quota_exceeded", False),
        (401, "auth_error", False),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
        (400, "bad_request", False),
        (422, "bad_request", False),
        (302, "bad_request", False),
    ],
)
def test_status_codes_map_to_safe_errors(status: int, code: str, retryable: bool) -> None:
    with pytest.raises(SearchProviderError) as caught:
        _search(_ok({"detail": f"secret detail {KEY}"}, status))
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert KEY not in str(caught.value) and "secret detail" not in str(caught.value)


@pytest.mark.parametrize("body", [b"not json", b'{"nope": 1}', b'{"results": "str"}', b"[]"])
def test_malformed_responses_are_bad_response(body: bytes) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    with pytest.raises(SearchProviderError) as caught:
        _search(transport)
    assert caught.value.code == "bad_response" and not caught.value.retryable


def test_oversized_response_is_rejected() -> None:
    big = b'{"results": [], "pad": "' + b"x" * MAX_RESPONSE_BYTES + b'"}'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=big))
    with pytest.raises(SearchProviderError) as caught:
        _search(transport)
    assert caught.value.code == "bad_response"


def test_timeouts_and_network_errors_are_retryable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler, code in ((timeout, "timeout"), (refused, "network_error")):
        with pytest.raises(SearchProviderError) as caught:
            _search(httpx.MockTransport(handler))
        assert (caught.value.code, caught.value.retryable) == (code, True)
