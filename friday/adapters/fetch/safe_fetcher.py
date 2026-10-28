import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx

from friday.adapters.fetch.base import FetchedPage, FetchError
from friday.adapters.fetch.url_policy import IPAddress, ParsedUrl, validate_addresses, validate_url

USER_AGENT = "FRIDAY-Research/0.1 (personal assistant; read-only page fetch)"
ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_BYTES = 1_500_000
MAX_REDIRECTS = 3

Resolver = Callable[[str], Awaitable[list[str]]]


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


class SafeFetcher:
    """Fetches public web pages only.

    Each hop is validated (URL gate, DNS answers, every address public), then the connection is
    pinned to a validated IP while keeping the original Host header and TLS name, so a later DNS
    answer cannot redirect it. Redirects are followed manually and re-validated.
    """

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    async def fetch(self, url: str, timeout_seconds: float) -> FetchedPage:
        try:
            async with asyncio.timeout(timeout_seconds):
                return await self._follow(url, timeout_seconds)
        except TimeoutError as exc:
            raise FetchError("timeout") from exc

    async def _follow(self, start_url: str, timeout_seconds: float) -> FetchedPage:
        current = start_url
        async with httpx.AsyncClient(
            transport=self._transport, follow_redirects=False, trust_env=False
        ) as client:
            for _ in range(self._max_redirects + 1):
                parsed = validate_url(current)
                addresses = await self._resolve(parsed)
                response = await self._get_pinned(client, parsed, addresses, timeout_seconds)
                client.cookies.clear()  # never carry cookies between hops
                if response.status in REDIRECT_STATUSES:
                    if not response.location:
                        raise FetchError("http_error")
                    current = urljoin(current, response.location)
                    continue
                return FetchedPage(
                    url=current,
                    status=response.status,
                    content_type=response.content_type,
                    text=response.text,
                    size_bytes=response.size,
                    retrieved_at=datetime.now(UTC),
                )
        raise FetchError("too_many_redirects")

    async def _resolve(self, parsed: ParsedUrl) -> list[IPAddress]:
        if parsed.is_ip_literal:
            return validate_addresses([parsed.host])
        try:
            answers = await self._resolver(parsed.host)
        except OSError as exc:
            raise FetchError("network_error") from exc
        return validate_addresses(answers)

    async def _get_pinned(
        self,
        client: httpx.AsyncClient,
        parsed: ParsedUrl,
        addresses: list[IPAddress],
        timeout_seconds: float,
    ) -> "_Response":
        last_error: FetchError | None = None
        for ip in addresses:
            try:
                return await self._request(client, parsed, ip, timeout_seconds)
            except _ConnectFailed as exc:
                last_error = FetchError("network_error")
                last_error.__cause__ = exc
        raise last_error or FetchError("network_error")

    async def _request(
        self, client: httpx.AsyncClient, parsed: ParsedUrl, ip: IPAddress, timeout_seconds: float
    ) -> "_Response":
        netloc_ip = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
        port = f":{parsed.port}" if parsed.port else ""
        pinned_url = f"{parsed.scheme}://{netloc_ip}{port}{parsed.path_and_query}"
        host_header = parsed.host + (f":{parsed.port}" if parsed.port else "")
        headers = {
            "Host": host_header,
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        extensions = {"sni_hostname": parsed.host} if parsed.scheme == "https" else {}
        try:
            async with client.stream(
                "GET", pinned_url, headers=headers, extensions=extensions, timeout=timeout_seconds
            ) as response:
                return await self._read(response)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise _ConnectFailed from exc
        except httpx.TimeoutException as exc:
            raise FetchError("timeout") from exc
        except httpx.HTTPError as exc:
            raise FetchError("network_error") from exc

    async def _read(self, response: httpx.Response) -> "_Response":
        status = response.status_code
        if status in REDIRECT_STATUSES:
            return _Response(status, "", "", 0, response.headers.get("location"))
        if not 200 <= status < 300:
            raise FetchError("http_error")
        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type not in ALLOWED_CONTENT_TYPES:
            raise FetchError("unsupported_content_type")
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise FetchError("too_large")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():  # decoded bytes: bounds compression bombs too
            total += len(chunk)
            if total > self._max_bytes:
                raise FetchError("too_large")
            chunks.append(chunk)
        body = b"".join(chunks)
        return _Response(status, media_type, _decode(body, response), total, None)


class _ConnectFailed(Exception):
    pass


class _Response:
    def __init__(
        self, status: int, content_type: str, text: str, size: int, location: str | None
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.text = text
        self.size = size
        self.location = location


def _decode(body: bytes, response: httpx.Response) -> str:
    charset = response.charset_encoding or "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")
