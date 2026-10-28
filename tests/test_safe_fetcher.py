import asyncio
import gzip
from typing import Any

import httpx
import pytest

from friday.adapters.fetch.base import FetchError
from friday.adapters.fetch.safe_fetcher import MAX_REDIRECTS, SafeFetcher

PUBLIC = "93.184.216.34"
HTML = "<html><head><title>Hi</title></head><body>hello</body></html>"


class Resolver:
    """Fake DNS: name -> list of answers (a list of lists = a different answer per lookup)."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table
        self.lookups: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        self.lookups.append(host)
        if host not in self.table:
            raise OSError("NXDOMAIN")
        answer = self.table[host]
        if answer and isinstance(answer[0], list):
            index = min(self.lookups.count(host) - 1, len(answer) - 1)
            return list(answer[index])
        return list(answer)


def html_response(body: str = HTML, **kwargs: Any) -> httpx.Response:
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/html; charset=utf-8"}, **kwargs
    )


def fetch(
    url: str, handler: Any, resolver: Resolver | None = None, timeout: float = 5, **options: Any
):  # type: ignore[no-untyped-def]
    resolver = resolver or Resolver({"example.com": [PUBLIC]})
    fetcher = SafeFetcher(resolver=resolver, transport=httpx.MockTransport(handler), **options)
    return asyncio.run(fetcher.fetch(url, timeout))


def error_code(url: str, handler: Any, resolver: Resolver | None = None, **options: Any) -> str:
    with pytest.raises(FetchError) as caught:
        fetch(url, handler, resolver, **options)
    return caught.value.code


# ----- the connection is pinned to a validated address -----


def test_request_goes_to_the_validated_ip_with_original_host_and_sni() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return html_response()

    page = fetch("https://example.com/path?q=1", handler)
    [request] = seen
    assert (
        request.url.host == PUBLIC
        and str(request.url.path) == "/path"
        and request.url.query == b"q=1"
    )
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"
    assert "cookie" not in request.headers and "authorization" not in request.headers
    assert request.headers["user-agent"].startswith("FRIDAY-Research")
    assert (page.url, page.status, page.content_type) == (
        "https://example.com/path?q=1",
        200,
        "text/html",
    )
    assert "hello" in page.text and page.size_bytes == len(HTML.encode())


def test_ipv6_answers_are_bracketed_in_the_pinned_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return html_response()

    fetch("http://example.com/", handler, Resolver({"example.com": ["2606:4700:4700::1111"]}))
    assert seen[0].url.host == "2606:4700:4700::1111" and seen[0].headers["host"] == "example.com"


def test_dns_rebinding_cannot_redirect_the_connection() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return html_response()

    # A hostile resolver answers with a public IP first and a private one on any later lookup.
    resolver = Resolver({"example.com": [[PUBLIC], ["10.0.0.5"]]})
    fetch("http://example.com/", handler, resolver)
    assert [r.url.host for r in seen] == [PUBLIC] and resolver.lookups == ["example.com"]


def test_a_single_private_answer_among_public_ones_blocks_the_fetch() -> None:
    calls: list[Any] = []
    resolver = Resolver({"example.com": [PUBLIC, "169.254.169.254"]})
    code = error_code("http://example.com/", lambda r: calls.append(r) or html_response(), resolver)
    assert code == "blocked_address" and calls == []


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://127.0.0.1/", "blocked_address"),
        ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
        ("http://[::1]/", "blocked_address"),
        ("http://[::ffff:10.0.0.1]/", "blocked_address"),
        ("http://localhost/", "blocked_url"),
        ("http://2130706433/", "blocked_url"),
        ("file:///etc/passwd", "blocked_url"),
        ("http://user:pw@example.com/", "blocked_url"),
        ("http://example.com:8080/", "blocked_url"),
    ],
)
def test_dangerous_targets_never_produce_a_network_request(url: str, code: str) -> None:
    calls: list[Any] = []
    assert error_code(url, lambda r: calls.append(r) or html_response()) == code
    assert calls == []


def test_unresolvable_hosts_report_a_generic_network_error() -> None:
    assert error_code("http://nxdomain.example/", lambda r: html_response()) == "network_error"


# ----- redirects are re-validated hop by hop -----


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def test_relative_and_cross_host_redirects_are_followed_with_validation() -> None:
    resolver = Resolver({"example.com": [PUBLIC], "docs.example.org": ["8.8.8.8"]})
    hops: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append((request.url.host, request.headers["host"]))
        if len(hops) == 1:
            return redirect("/moved")
        if len(hops) == 2:
            return redirect("https://docs.example.org/final")
        return html_response()

    page = fetch("http://example.com/start", handler, resolver)
    assert hops == [
        (PUBLIC, "example.com"),
        (PUBLIC, "example.com"),
        ("8.8.8.8", "docs.example.org"),
    ]
    assert page.url == "https://docs.example.org/final"


@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("http://127.0.0.1/admin", "blocked_address"),
        ("http://169.254.169.254/latest/meta-data/iam/", "blocked_address"),
        ("http://[::1]:80/", "blocked_address"),
        ("http://internal.example/", "blocked_address"),  # resolves to a private address
        ("file:///etc/passwd", "blocked_url"),
        ("ftp://example.com/x", "blocked_url"),
        ("http://localhost/", "blocked_url"),
        ("http://example.com:6379/", "blocked_url"),
    ],
)
def test_redirects_to_unsafe_targets_are_blocked(location: str, code: str) -> None:
    resolver = Resolver({"example.com": [PUBLIC], "internal.example": ["10.1.2.3"]})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return redirect(location)

    assert error_code("http://example.com/", handler, resolver) == code
    assert len(seen) == 1  # only the first, validated hop ever left the process


def test_redirect_loops_and_long_chains_are_cut_off() -> None:
    hops = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hops
        hops += 1
        return redirect("/again")

    assert error_code("http://example.com/", handler) == "too_many_redirects"
    assert hops == MAX_REDIRECTS + 1


def test_a_redirect_without_a_location_is_an_error() -> None:
    assert error_code("http://example.com/", lambda r: httpx.Response(302)) == "http_error"


def test_cookies_are_never_carried_between_hops() -> None:
    sent: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie"))
        if len(sent) == 1:
            return httpx.Response(
                302, headers={"location": "/next", "set-cookie": "sid=secret; Path=/"}
            )
        return html_response()

    fetch("http://example.com/", handler)
    assert sent == [None, None]


# ----- response limits -----


@pytest.mark.parametrize(
    "content_type", ["application/pdf", "application/json", "image/png", "text/javascript", ""]
)
def test_only_text_like_content_types_are_accepted(content_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"data", headers={"content-type": content_type} if content_type else {}
        )

    assert error_code("http://example.com/", handler) == "unsupported_content_type"


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "TEXT/HTML; charset=ISO-8859-1", "application/xhtml+xml", "text/plain"],
)
def test_allowed_content_types(content_type: str) -> None:
    page = fetch(
        "http://example.com/",
        lambda r: httpx.Response(200, content=b"ok", headers={"content-type": content_type}),
    )
    assert page.text == "ok"


def test_oversized_bodies_are_rejected_by_header_and_by_streaming() -> None:
    def declared(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": "text/plain", "content-length": "99999999"}
        return httpx.Response(200, content=b"x", headers=headers)

    def undeclared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"y" * 5000, headers={"content-type": "text/plain"})

    assert error_code("http://example.com/", declared, max_bytes=1000) == "too_large"
    assert error_code("http://example.com/", undeclared, max_bytes=1000) == "too_large"


def test_a_compression_bomb_is_bounded_by_the_decoded_size() -> None:
    bomb = gzip.compress(b"a" * 5_000_000)
    assert len(bomb) < 10_000  # tiny on the wire, huge once decoded

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=bomb, headers={"content-type": "text/plain", "content-encoding": "gzip"}
        )

    assert error_code("http://example.com/", handler, max_bytes=100_000) == "too_large"


def test_error_statuses_and_slow_servers_map_to_stable_codes() -> None:
    assert error_code("http://example.com/", lambda r: httpx.Response(404)) == "http_error"
    assert error_code("http://example.com/", lambda r: httpx.Response(503)) == "http_error"

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return html_response()

    with pytest.raises(FetchError) as caught:
        fetch("http://example.com/", slow, timeout=0.1)
    assert caught.value.code == "timeout"


def test_connection_failures_never_leak_details() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect to 10.0.0.9:5432 refused", request=request)

    with pytest.raises(FetchError) as caught:
        fetch("http://example.com/", refused)
    assert caught.value.code == "network_error" and "10.0.0.9" not in str(caught.value)


def test_each_validated_address_is_tried_in_turn() -> None:
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.host)
        if request.url.host == "1.1.1.1":
            raise httpx.ConnectError("down", request=request)
        return html_response()

    fetch("http://example.com/", handler, Resolver({"example.com": ["1.1.1.1", "8.8.8.8"]}))
    assert tried == ["1.1.1.1", "8.8.8.8"]
