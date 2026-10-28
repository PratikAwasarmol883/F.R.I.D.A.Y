"""URL and address rules for fetching pages. Pure functions; every SSRF class is tested here."""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from friday.adapters.fetch.base import FetchError

MAX_URL_CHARS = 2048
ALLOWED_PORTS = frozenset({80, 443})
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".localdomain",
    ".home.arpa",
    ".lan",
    ".intranet",
)
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]+|[0-9]+)$")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class ParsedUrl:
    scheme: str
    host: str  # lowercase, IDNA-encoded, no trailing dot, no brackets
    port: int | None
    path_and_query: str  # always starts with "/"
    is_ip_literal: bool

    @property
    def default_port(self) -> int:
        return 443 if self.scheme == "https" else 80


def validate_url(url: str) -> ParsedUrl:
    """Reject anything that is not a plain public-looking http(s) URL. Raises FetchError."""
    if (
        not url
        or len(url) > MAX_URL_CHARS
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url)
    ):
        raise FetchError("blocked_url")
    try:
        parts = urlsplit(url)
        port = parts.port
        raw_host = parts.hostname
    except ValueError as exc:
        raise FetchError("blocked_url") from exc
    if parts.scheme not in {"http", "https"} or not raw_host:
        raise FetchError("blocked_url")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise FetchError("blocked_url")
    if port is not None and port not in ALLOWED_PORTS:
        raise FetchError("blocked_url")

    host = raw_host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise FetchError("blocked_url") from exc
    if not host:
        raise FetchError("blocked_url")

    is_ip = True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_ip = False
        labels = host.split(".")
        if all(_NUMERIC_LABEL.match(label) for label in labels):
            raise FetchError("blocked_url") from None  # decimal/octal/hex or short IPv4 forms
        if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
            raise FetchError("blocked_url") from None
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return ParsedUrl(parts.scheme, host, port, path + query, is_ip)


def is_blocked_ip(ip: IPAddress) -> bool:
    """True for any address that is not an ordinary public unicast address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_blocked_ip(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return is_blocked_ip(ip.sixtofour)
        if ip.teredo is not None or ip in _NAT64_LOCAL or ip in _IPV4_COMPATIBLE:
            return True
        if ip.is_site_local:  # deprecated fec0::/10, still routed inside some networks
            return True
        if ip in _NAT64:
            return is_blocked_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    elif ip in _CGNAT:
        return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_addresses(addresses: list[str]) -> list[IPAddress]:
    """Every resolved address must be public; one bad address rejects the whole host."""
    parsed: list[IPAddress] = []
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])  # drop IPv6 zone ids
        except ValueError as exc:
            raise FetchError("blocked_address") from exc
        if is_blocked_ip(ip):
            raise FetchError("blocked_address")
        parsed.append(ip)
    if not parsed:
        raise FetchError("network_error")
    return parsed


def canonical_url(url: str) -> str | None:
    """Comparable form of a URL (scheme/host lowercased, default port and fragment dropped)."""
    try:
        parsed = validate_url(url)
    except FetchError:
        return None
    port = f":{parsed.port}" if parsed.port and parsed.port != parsed.default_port else ""
    return f"{parsed.scheme}://{parsed.host}{port}{parsed.path_and_query}"
