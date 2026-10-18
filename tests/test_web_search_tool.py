import asyncio

import pytest
from pydantic import ValidationError

from friday.adapters.search.base import SearchHit, SearchProviderError
from friday.adapters.search.fake import FakeSearchProvider
from friday.core.config import Settings
from friday.orchestration.policy import Allow, Deny, DenyReason, PolicyContext, evaluate
from friday.orchestration.tool_contracts import ToolError
from friday.orchestration.tool_registry import ToolRegistry
from friday.tools.registry import build_registry
from friday.tools.web_search.schemas import WebSearchInput
from friday.tools.web_search.tool import WebSearchTool


def _tool(provider: FakeSearchProvider, **overrides: int | float) -> WebSearchTool:
    params: dict[str, int | float] = {
        "timeout_seconds": 4.0,
        "max_results": 5,
        "max_calls_per_ask": 3,
        "daily_limit": 50,
    }
    params.update(overrides)
    return WebSearchTool(provider, **params)  # type: ignore[arg-type]


def _run(tool: WebSearchTool, **arguments: object):  # type: ignore[no-untyped-def]
    return asyncio.run(tool.run(WebSearchInput(**arguments)))  # type: ignore[arg-type]


def _ctx() -> PolicyContext:
    return PolicyContext(
        enabled_tools=frozenset({"web_search"}),
        available_tools=frozenset({"web_search"}),
        device_scopes=frozenset({"tool:web_search"}),
        max_total_calls_per_ask=3,
    )


def _registry(provider: FakeSearchProvider) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_tool(provider))
    return registry


def test_contract_is_valid_read_only_and_marks_results_untrusted() -> None:
    contract = _tool(FakeSearchProvider()).contract
    contract.validate()
    assert contract.risk_class.value == "read_only" and contract.returns_untrusted_content
    assert contract.required_scope == "tool:web_search" and contract.max_retries == 1


@pytest.mark.parametrize(
    "query",
    [
        "visit https://evil.example/?d=private",
        "ftp://x",
        "my key is fri_abcdefghij1234567890",
        "token tvly-abcdefgh12345678",
        "Bearer abc.def.ghi",
        "a" * 40,
        "line one\nline two",
        "tab\there",
        "   ",
    ],
)
def test_guard_rejects_queries_that_could_smuggle_data(query: str) -> None:
    provider = FakeSearchProvider()
    decision = evaluate(_registry(provider), "web_search", {"query": query}, _ctx())
    assert isinstance(decision, Deny) and decision.reason is DenyReason.GUARD_REJECTED
    assert provider.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": "x" * 201},
        {"query": "ok", "max_results": 0},
        {"query": "ok", "max_results": 6},
        {"query": "ok", "max_results": "3"},
        {"query": "ok", "site": "example.com"},
    ],
)
def test_invalid_arguments_are_denied_by_policy(arguments: dict[str, object]) -> None:
    decision = evaluate(_registry(FakeSearchProvider()), "web_search", arguments, _ctx())
    assert isinstance(decision, Deny) and decision.reason is DenyReason.INVALID_ARGUMENTS


def test_plain_queries_are_allowed() -> None:
    decision = evaluate(
        _registry(FakeSearchProvider()), "web_search", {"query": "fastapi lifespan events"}, _ctx()
    )
    assert isinstance(decision, Allow)


def test_results_are_sanitized_capped_and_unsafe_urls_dropped() -> None:
    hits = [
        SearchHit(
            "<b>Good</b>\N{ZERO WIDTH SPACE} title",
            "https://good.example/a",
            "snippet <script>x()</script> &amp; more",
        ),
        SearchHit("JS", "javascript:alert(1)", "bad scheme"),
        SearchHit("Creds", "https://u:p@host.example", "has credentials"),
        SearchHit("Long", "https://long.example", "w " * 600),
        SearchHit("Third", "https://third.example", "t"),
    ]
    output = _run(_tool(FakeSearchProvider(hits=hits), max_results=2), query="q")
    assert [r.url for r in output.results] == ["https://good.example/a", "https://long.example"]
    first = output.results[0]
    assert (
        first.title == "Good title"
        and "<" not in first.snippet
        and "\N{ZERO WIDTH SPACE}" not in first.title
    )
    assert len(output.results[1].snippet) <= 500


def test_provider_gets_trimmed_query_capped_results_and_the_tool_timeout() -> None:
    provider = FakeSearchProvider()
    _run(_tool(provider, max_results=2, timeout_seconds=4.0), query="  hello  ", max_results=5)
    assert provider.calls == [("hello", 2, 4.0)]


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (SearchProviderError("timeout", retryable=True), True),
        (SearchProviderError("quota_exceeded"), False),
    ],
)
def test_provider_errors_become_tool_errors(error: SearchProviderError, retryable: bool) -> None:
    with pytest.raises(ToolError) as caught:
        _run(_tool(FakeSearchProvider(error=error)), query="q")
    assert caught.value.code == error.code and caught.value.retryable is retryable


def test_input_model_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        WebSearchInput(query="q", extra=1)  # type: ignore[call-arg]


def test_registry_builder_registers_only_configured_tools() -> None:
    assert build_registry(Settings(_env_file=None)).all() == []
    configured = Settings(_env_file=None, search_api_key="tvly-abcdefgh12345678")  # type: ignore[arg-type]
    assert [t.contract.name for t in build_registry(configured).all()] == ["web_search"]
    injected = build_registry(Settings(_env_file=None), FakeSearchProvider())
    assert [t.contract.name for t in injected.all()] == ["web_search"]


def test_enabled_tool_names_parse_from_comma_separated_setting() -> None:
    assert Settings(_env_file=None, enabled_tools=" web_search , other ").enabled_tool_names == {
        "web_search",
        "other",
    }
    assert Settings(_env_file=None, enabled_tools="").enabled_tool_names == frozenset()
