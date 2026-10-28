import ipaddress

import pytest

from friday.adapters.fetch.base import FetchError
from friday.adapters.fetch.url_policy import is_blocked_ip, validate_addresses, validate_url

BLOCKED_IPS = """
127.0.0.1 127.1.2.3 10.0.0.1 172.16.0.1 172.31.255.255 192.168.1.1 169.254.169.254 169.254.0.1
169.254.170.2 0.0.0.0 100.64.0.1 100.127.255.255 224.0.0.1 239.255.255.255 240.0.0.1
255.255.255.255 192.0.0.1 192.0.2.1 198.18.0.1 198.51.100.1 203.0.113.1
::1 :: fc00::1 fd12:3456::1 fe80::1 fec0::1 ff02::1 ::ffff:127.0.0.1 ::ffff:10.0.0.1
::ffff:169.254.169.254 2002:7f00:1::1 2002:a9fe:a9fe::1 64:ff9b::7f00:1 64:ff9b::a9fe:a9fe
64:ff9b:1::1 ::127.0.0.1 2001::1 100::1 2001:db8::1 2001:10::1
""".split()
ALLOWED_IPS = """
93.184.216.34 8.8.8.8 1.1.1.1 151.101.1.69 2606:4700:4700::1111 2001:4860:4860::8888
64:ff9b::808:808 ::ffff:8.8.8.8 2a00:1450:4001:81b::200e
""".split()


@pytest.mark.parametrize("address", BLOCKED_IPS)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", ALLOWED_IPS)
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(address)) is False


def test_one_bad_address_rejects_the_whole_host() -> None:
    with pytest.raises(FetchError) as caught:
        validate_addresses(["93.184.216.34", "10.0.0.5"])
    assert caught.value.code == "blocked_address"
    with pytest.raises(FetchError) as caught:
        validate_addresses(["2606:4700:4700::1111", "::1"])
    assert caught.value.code == "blocked_address"


def test_unparseable_or_empty_answers_are_rejected() -> None:
    with pytest.raises(FetchError, match="blocked_address"):
        validate_addresses(["not-an-ip"])
    with pytest.raises(FetchError, match="network_error"):
        validate_addresses([])
    assert [str(ip) for ip in validate_addresses(["8.8.8.8"])] == ["8.8.8.8"]


def test_ipv6_zone_ids_are_stripped_before_checking() -> None:
    with pytest.raises(FetchError, match="blocked_address"):
        validate_addresses(["fe80::1%eth0"])


BLOCKED_URLS = [
    "",
    "   ",
    "ftp://example.com/file",
    "file:///etc/passwd",
    "gopher://example.com/",
    "javascript:alert(1)",
    "data:text/html,<h1>x</h1>",
    "//example.com/path",
    "http://",
    "https:///path",
    "http://user:pass@example.com/",
    "http://user@example.com/",
    "http://example.com@evil.example/",
    "http://example.com:8080/",
    "http://example.com:22/",
    "https://example.com:444/",
    "http://localhost/",
    "http://LOCALHOST/",
    "http://localhost./",
    "http://app.localhost/",
    "http://printer.local/",
    "http://db.internal/",
    "http://host.localdomain/",
    "http://router.home.arpa/",
    "http://2130706433/",
    "http://017700000001/",
    "http://0x7f000001/",
    "http://0x7f.0.0.1/",
    "http://127.1/",
    "http://0/",
    "http://1.1/",
    "http://example.com/has space",
    "http://example.com/\x00",
    "http://example.com/\r\nHost: evil",
    "http://exa mple.com/",
    "http://[::1/",
    "http://example.com:notaport/",
    "http://example.com/" + "a" * 2100,
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_unsafe_urls_are_rejected_before_any_network_activity(url: str) -> None:
    with pytest.raises(FetchError) as caught:
        validate_url(url)
    assert caught.value.code == "blocked_url"


@pytest.mark.parametrize(
    ("url", "host", "port", "path"),
    [
        ("https://example.com/a/b?q=1#frag", "example.com", None, "/a/b?q=1"),
        ("HTTP://Example.COM", "example.com", None, "/"),
        ("https://example.com:443/x", "example.com", 443, "/x"),
        ("http://example.com:80", "example.com", 80, "/"),
        ("https://example.com./", "example.com", None, "/"),
        ("https://abc.def/", "abc.def", None, "/"),  # hex-looking letters are not an IP
        ("https://1e100.net/", "1e100.net", None, "/"),
        ("https://bücher.example/", "xn--bcher-kva.example", None, "/"),
    ],
)
def test_ordinary_urls_are_normalized(url: str, host: str, port: int | None, path: str) -> None:
    parsed = validate_url(url)
    assert (parsed.host, parsed.port, parsed.path_and_query) == (host, port, path)
    assert parsed.is_ip_literal is False


@pytest.mark.parametrize("url", ["http://93.184.216.34/", "http://[2606:4700:4700::1111]/"])
def test_canonical_ip_literals_pass_the_url_gate_and_face_the_address_check(url: str) -> None:
    parsed = validate_url(url)
    assert parsed.is_ip_literal is True
    assert validate_addresses([parsed.host])


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://10.0.0.1/",
    ],
)
def test_blocked_ip_literals_pass_the_url_gate_but_never_the_address_check(url: str) -> None:
    parsed = validate_url(url)
    with pytest.raises(FetchError, match="blocked_address"):
        validate_addresses([parsed.host])
