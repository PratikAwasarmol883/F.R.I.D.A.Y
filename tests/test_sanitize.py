import pytest

from friday.orchestration.sanitize import UNTRUSTED_NOTICE, clean_text, sanitize_url, wrap_untrusted


def test_markup_entities_and_invisible_characters_are_removed() -> None:
    raw = (
        "<b>Hello</b>\N{ZERO WIDTH SPACE} <script>alert(1)</script> &lt;img src=x&gt;"
        "\N{RIGHT-TO-LEFT OVERRIDE} world\x00"
    )
    cleaned = clean_text(raw, 200)
    assert "<" not in cleaned and ">" not in cleaned
    assert (
        "\N{ZERO WIDTH SPACE}" not in cleaned
        and "\N{RIGHT-TO-LEFT OVERRIDE}" not in cleaned
        and "\x00" not in cleaned
    )
    assert cleaned.startswith("Hello")
    assert "  " not in cleaned


def test_text_is_truncated_with_ellipsis() -> None:
    cleaned = clean_text("word " * 200, 50)
    assert len(cleaned) <= 50 and cleaned.endswith("\N{HORIZONTAL ELLIPSIS}")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "https://user:pass@example.com/",
        "https://",
        "http://exa mple.com",
        "https://example.com/" + "a" * 2100,
        "",
    ],
)
def test_unsafe_urls_are_dropped(url: str) -> None:
    assert sanitize_url(url) is None


@pytest.mark.parametrize("url", ["https://example.com/a?b=1#c", "http://docs.python.org/3/"])
def test_plain_http_urls_are_kept(url: str) -> None:
    assert sanitize_url(url) == url


def test_untrusted_wrapper_prefixes_payload() -> None:
    wrapped = wrap_untrusted('{"a": 1}')
    assert wrapped.startswith(UNTRUSTED_NOTICE) and wrapped.endswith('{"a": 1}')
