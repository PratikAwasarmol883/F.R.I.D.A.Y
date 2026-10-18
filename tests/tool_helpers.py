import asyncio
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from friday.adapters.llm import LLMMessage, LLMResult, ToolCall, ToolSpec
from friday.adapters.llm.fake import FakeLLMProvider
from friday.adapters.search.fake import FakeSearchProvider
from friday.orchestration.executor import ExecutionEntry, ToolLoop
from friday.orchestration.policy import PolicyContext
from friday.orchestration.tool_registry import ToolRegistry
from friday.tools.web_search.tool import WebSearchTool

_counter = 0


def tool_turn(*calls: tuple[str, Any], text: str = "") -> LLMResult:
    """A scripted model turn that requests tool calls: (tool_name, arguments)."""
    global _counter
    tool_calls = []
    for name, arguments in calls:
        _counter += 1
        tool_calls.append(ToolCall(f"toolu_{_counter}", name, arguments))
    return LLMResult(
        text=text,
        input_tokens=10,
        output_tokens=5,
        model="fake-model",
        provider="fake",
        tool_calls=tuple(tool_calls),
        stop_reason="tool_use",
    )


def final(text: str = "final answer") -> LLMResult:
    return LLMResult(
        text=text, input_tokens=10, output_tokens=5, model="fake-model", provider="fake"
    )


def search(query: str = "python docs", **extra: Any) -> tuple[str, dict[str, Any]]:
    return ("web_search", {"query": query, **extra})


def search_tool(provider: FakeSearchProvider, **overrides: Any) -> WebSearchTool:
    params: dict[str, Any] = {
        "timeout_seconds": 2.0,
        "max_results": 5,
        "max_calls_per_ask": 3,
        "daily_limit": 50,
    }
    params.update(overrides)
    return WebSearchTool(provider, **params)


def search_registry(provider: FakeSearchProvider, **overrides: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(search_tool(provider, **overrides))
    return registry


class MemoryRecorder:
    def __init__(self) -> None:
        self.entries: list[ExecutionEntry] = []

    async def record(self, entry: ExecutionEntry) -> uuid.UUID:
        self.entries.append(entry)
        return uuid.uuid4()


class SlowLLM(FakeLLMProvider):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    async def complete(  # type: ignore[override]
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        max_output_tokens: int,
        timeout_seconds: float,
        tools: Sequence[ToolSpec] = (),
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> LLMResult:
        await asyncio.sleep(self.delay)
        return await super().complete(
            system=system,
            messages=messages,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            tools=tools,
            tool_choice=tool_choice,
        )


def policy_ctx(**overrides: Any) -> PolicyContext:
    fields: dict[str, Any] = {
        "enabled_tools": frozenset({"web_search"}),
        "available_tools": frozenset({"web_search"}),
        "device_scopes": frozenset({"tool:web_search"}),
        "max_total_calls_per_ask": 3,
    }
    fields.update(overrides)
    return PolicyContext(**fields)


def make_loop(
    llm: FakeLLMProvider, registry: ToolRegistry, recorder: MemoryRecorder, **overrides: Any
) -> ToolLoop:
    params: dict[str, Any] = {
        "system_prompt": "sys",
        "max_output_tokens": 100,
        "llm_timeout_seconds": 5.0,
        "deadline_seconds": 5.0,
        "check_budget": lambda spent: None,
        "retry_backoff": (0.0, 0.0),
    }
    params.update(overrides)
    return ToolLoop(llm, registry, recorder, **params)
