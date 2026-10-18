import asyncio
import os

import pytest

from friday.adapters.llm.anthropic_provider import AnthropicProvider
from friday.core.config import Settings
from friday.domain.enums import ToolStatus
from friday.orchestration.policy import PolicyContext
from friday.tools.registry import build_registry

from .tool_helpers import MemoryRecorder, make_loop

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("SEARCH_API_KEY")),
    reason="ANTHROPIC_API_KEY and SEARCH_API_KEY not set (opt-in live test)",
)


def test_real_model_uses_real_search_through_the_policy_loop() -> None:
    settings = Settings(_env_file=None)
    registry = build_registry(settings)
    llm = AnthropicProvider(os.environ["ANTHROPIC_API_KEY"], settings.llm_model)
    ctx = PolicyContext(
        enabled_tools=frozenset({"web_search"}),
        available_tools=frozenset({"web_search"}),
        device_scopes=frozenset({"tool:web_search"}),
        max_total_calls_per_ask=3,
    )
    recorder = MemoryRecorder()
    loop = make_loop(
        llm, registry, recorder, system_prompt="Use web_search, then answer in one sentence."
    )
    outcome = asyncio.run(
        loop.run([], "Use web search to find the official Python downloads page URL.", ctx)
    )
    assert outcome.text.strip()
    assert any(use.status is ToolStatus.SUCCEEDED for use in outcome.tools_used)
    assert outcome.sources, "expected at least one source URL from the real search results"
