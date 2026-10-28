import asyncio
import hashlib
from datetime import datetime
from typing import Any

import pytest

from friday.adapters.fetch.base import FetchError
from friday.adapters.fetch.fake import FakeFetcher
from friday.adapters.search.base import SearchHit
from friday.adapters.search.fake import FakeSearchProvider
from friday.core.config import Settings
from friday.orchestration.policy import (
    Allow,
    Deny,
    DenyReason,
    PolicyContext,
    advertised_tools,
    evaluate,
)
from friday.orchestration.tool_contracts import ToolError
from friday.tools.research import build_research_registry
from friday.tools.web_fetch.extract import extract_text
from friday.tools.web_fetch.ledger import SourceLedger
from friday.tools.web_fetch.schemas import WebFetchInput
from friday.tools.web_fetch.tool import MODEL_TEXT_CHARS, WebFetchTool
from friday.tools.web_search.schemas import WebSearchInput

ZWSP = "\N{ZERO WIDTH SPACE}"
BIDI = "\N{RIGHT-TO-LEFT OVERRIDE}"
PAGE = """<!doctype html><html><head><title>  My   Page </title><style>p{color:red}</style></head>
<body><nav>NAVLINK Home</nav><h1>Hello world</h1><script>alert('SCRIPTTEXT')</script>
<p>Tom &amp; Jerry &lt;3 <b>cartoons</b></p><!-- HIDDENCOMMENT -->
<footer>FOOTERTEXT</footer></body></html>"""


# ----- extraction -----


def test_extracts_title_and_readable_text_only() -> None:
    result = extract_text(PAGE, "text/html", 10_000)
    assert result.title == "My Page"
    assert "Hello world" in result.text and "Tom & Jerry <3 cartoons" in result.text
    for hidden in (
        "NAVLINK",
        "SCRIPTTEXT",
        "color:red",
        "FOOTERTEXT",
        "HIDDENCOMMENT",
        "<script",
        "<nav",
        "</",
    ):
        assert hidden not in result.text


def test_invisible_characters_are_removed_and_text_is_truncated() -> None:
    html = f"<p>a{ZWSP}b{BIDI}c</p><p>{'word ' * 500}</p>"
    result = extract_text(html, "text/html", 100)
    assert ZWSP not in result.text and BIDI not in result.text and result.text.startswith("abc")
    assert len(result.text) <= 100 and result.text.endswith("…")


def test_plain_text_and_malformed_markup_never_raise() -> None:
    assert extract_text("just   some\ntext", "text/plain", 100).text == "just some text"
    broken = "<p>before</p><script>never closed <p>after</p>"
    assert "before" in extract_text(broken, "text/html", 1000).text
    for junk in ["<<<>>>", "<a href=", "\x00\x01\x02", "<p" * 5000, "&#99999999;&bogus;"]:
        assert isinstance(extract_text(junk, "text/html", 1000).text, str)


# ----- guard: only URLs the run may legitimately fetch -----


def make_tool(
    question: str = "", **overrides: Any
) -> tuple[WebFetchTool, SourceLedger, FakeFetcher]:
    ledger, fetcher = SourceLedger(question), FakeFetcher()
    params: dict[str, Any] = {"timeout_seconds": 3.0, "max_calls_per_ask": 5, "daily_limit": 100}
    params.update(overrides)
    return WebFetchTool(fetcher, ledger, **params), ledger, fetcher


def guard(tool: WebFetchTool, url: str) -> str | None:
    return tool.guard(WebFetchInput(url=url))


def test_only_urls_from_search_results_or_the_question_may_be_fetched() -> None:
    tool, ledger, _ = make_tool("Please read https://example.com/a?x=1 and summarize")
    assert guard(tool, "https://example.com/a?x=1") is None  # the user wrote it
    assert guard(tool, "HTTPS://Example.COM:443/a?x=1#section") is None  # canonical equality
    assert guard(tool, "https://example.com/other") == "url_not_allowed"
    assert guard(tool, "https://evil.example/?leak=private+details") == "url_not_allowed"
    ledger.allow_urls(["https://docs.example/page"])
    assert guard(tool, "https://docs.example/page") is None
    assert guard(tool, "https://docs.example/page2") == "url_not_allowed"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://127.0.0.1/", "blocked_address"),
        ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
        ("http://[::1]/", "blocked_address"),
        ("file:///etc/passwd", "blocked_url"),
        ("http://localhost/", "blocked_url"),
        ("http://2130706433/", "blocked_url"),
        ("https://user:pw@example.com/", "blocked_url"),
        ("https://example.com:8443/", "blocked_url"),
        ("https://example.com/?token=fri_abcdefghij1234567890", "secret_in_url"),
        ("https://example.com/?k=sk-ant-abcdefghijklmnop", "secret_in_url"),
    ],
)
def test_dangerous_or_secret_bearing_urls_are_rejected_even_if_listed(url: str, code: str) -> None:
    tool, ledger, _ = make_tool()
    ledger.allow_urls([url])  # as if a hostile search result had listed it
    assert guard(tool, url) == code


def test_long_article_slugs_are_not_mistaken_for_secrets() -> None:
    tool, ledger, _ = make_tool()
    url = "https://news.example/2026/10/a-very-long-article-slug-about-the-history-of-computing"
    ledger.allow_urls([url])
    assert guard(tool, url) is None


def test_the_policy_denies_disallowed_urls_before_any_network_activity() -> None:
    tool, ledger, fetcher = make_tool()
    settings = Settings(_env_file=None)
    registry = build_research_registry(
        settings, ledger, FakeSearchProvider(), fetcher, max_searches=3, max_fetches=5
    )
    ctx = PolicyContext(
        enabled_tools=frozenset({"web_search", "web_fetch"}),
        available_tools=frozenset({"web_search", "web_fetch"}),
        device_scopes=frozenset({"tool:web_search", "tool:web_fetch"}),
        max_total_calls_per_ask=8,
    )
    denied = evaluate(registry, "web_fetch", {"url": "https://evil.example/x"}, ctx)
    assert isinstance(denied, Deny) and denied.reason is DenyReason.GUARD_REJECTED
    assert denied.detail == "url_not_allowed" and fetcher.calls == []
    ledger.allow_urls(["https://good.example/x"])
    assert isinstance(
        evaluate(registry, "web_fetch", {"url": "https://good.example/x"}, ctx), Allow
    )


def test_both_scopes_are_needed_to_see_both_tools() -> None:
    _, ledger, fetcher = make_tool()
    registry = build_research_registry(
        Settings(_env_file=None),
        ledger,
        FakeSearchProvider(),
        fetcher,
        max_searches=3,
        max_fetches=5,
    )
    names = frozenset({"web_search", "web_fetch"})

    def advertised(scopes: set[str]) -> list[str]:
        ctx = PolicyContext(names, names, frozenset(scopes), 8)
        return sorted(t.contract.name for t in advertised_tools(registry, ctx))

    assert advertised({"tool:web_search", "tool:web_fetch"}) == ["web_fetch", "web_search"]
    assert advertised({"tool:web_search"}) == ["web_search"]
    assert advertised(set()) == []


# ----- running -----


def run(tool: WebFetchTool, url: str) -> Any:
    return asyncio.run(tool.run(WebFetchInput(url=url)))


def test_a_fetch_returns_a_cited_source_and_records_it_in_the_ledger() -> None:
    tool, ledger, fetcher = make_tool("see https://example.com/a")
    fetcher.add_html("https://example.com/a", PAGE)
    output = run(tool, "https://example.com/a")
    assert (output.source_id, output.url, output.title) == (
        "S1",
        "https://example.com/a",
        "My Page",
    )
    assert datetime.fromisoformat(output.retrieved_at)
    [record] = ledger.sources
    page_text = extract_text(PAGE, "text/html", 20_000).text
    assert record.content_hash == hashlib.sha256(page_text.encode()).hexdigest()
    assert len(record.excerpt) <= 1000 and record.content_type == "text/html"
    assert fetcher.calls == [("https://example.com/a", 3.0)]


def test_repeat_fetches_dedupe_and_new_pages_get_new_ids() -> None:
    tool, ledger, fetcher = make_tool("https://example.com/a https://example.com/b")
    fetcher.add_html("https://example.com/a", PAGE)
    fetcher.add_html("https://example.com/b", PAGE, final_url="https://example.com/b-final")
    assert run(tool, "https://example.com/a").source_id == "S1"
    assert run(tool, "https://example.com/a").source_id == "S1"
    assert run(tool, "https://example.com/b").source_id == "S2"
    assert [s.url for s in ledger.sources] == [
        "https://example.com/a",
        "https://example.com/b-final",
    ]


def test_text_shown_to_the_model_is_capped_but_the_hash_covers_the_whole_page() -> None:
    tool, ledger, fetcher = make_tool("https://example.com/long")
    fetcher.add_html("https://example.com/long", "<p>" + "lorem ipsum " * 5000 + "</p>")
    output = run(tool, "https://example.com/long")
    assert len(output.text) <= MODEL_TEXT_CHARS
    assert ledger.sources[0].content_hash != hashlib.sha256(output.text.encode()).hexdigest()


def test_fetch_failures_become_non_retryable_tool_errors_with_stable_codes() -> None:
    tool, _, fetcher = make_tool("https://example.com/x")
    fetcher.pages["https://example.com/x"] = FetchError("blocked_address")
    with pytest.raises(ToolError) as caught:
        run(tool, "https://example.com/x")
    assert caught.value.code == "blocked_address" and caught.value.retryable is False


def test_stored_arguments_hide_query_strings_and_fragments() -> None:
    tool, _, _ = make_tool()
    masked = tool.redact_arguments({"url": "https://example.com/p?token=abc&q=private#frag"})
    assert masked == {"url": "https://example.com/p?…"}
    assert tool.redact_arguments({"url": "https://example.com/p"}) == {
        "url": "https://example.com/p"
    }
    assert tool.redact_arguments({"other": 1}) == {"other": 1}


def test_summary_exposes_host_and_source_but_not_page_text() -> None:
    tool, _, fetcher = make_tool("https://example.com/a")
    fetcher.add_html("https://example.com/a", PAGE)
    summary = tool.summarize(run(tool, "https://example.com/a"))
    assert summary.meta["host"] == "example.com" and summary.meta["source_id"] == "S1"
    assert "Hello world" not in str(summary.meta)
    assert [(s.title, s.url) for s in summary.sources] == [("My Page", "https://example.com/a")]


def test_search_results_unlock_exactly_those_urls_for_fetching() -> None:
    ledger, fetcher = SourceLedger("what is x?"), FakeFetcher()
    hits = [
        SearchHit("A", "https://a.example/page", "snippet"),
        SearchHit("B", "https://b.example/", "s"),
    ]
    registry = build_research_registry(
        Settings(_env_file=None),
        ledger,
        FakeSearchProvider(hits=hits),
        fetcher,
        max_searches=3,
        max_fetches=5,
    )
    search, fetch = registry.get("web_search"), registry.get("web_fetch")
    assert search is not None and fetch is not None
    assert fetch.guard(WebFetchInput(url="https://a.example/page")) == "url_not_allowed"
    asyncio.run(search.run(WebSearchInput(query="x")))
    assert fetch.guard(WebFetchInput(url="https://a.example/page")) is None
    assert fetch.guard(WebFetchInput(url="https://b.example/")) is None
    assert fetch.guard(WebFetchInput(url="https://c.example/")) == "url_not_allowed"


def test_research_registry_contracts_are_valid_read_only_and_capped() -> None:
    ledger, fetcher = SourceLedger(), FakeFetcher()
    registry = build_research_registry(
        Settings(_env_file=None),
        ledger,
        FakeSearchProvider(),
        fetcher,
        max_searches=2,
        max_fetches=4,
    )
    caps = {t.contract.name: t.contract.max_calls_per_ask for t in registry.all()}
    assert caps == {"web_search": 2, "web_fetch": 4}
    for tool in registry.all():
        tool.contract.validate()
        assert (
            tool.contract.risk_class.value == "read_only"
            and tool.contract.returns_untrusted_content
        )
