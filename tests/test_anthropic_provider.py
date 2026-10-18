import asyncio
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from anthropic import omit
from anthropic.types import Message

from friday.adapters.llm import (
    AssistantToolTurn,
    ChatMessage,
    ToolCall,
    ToolResult,
    ToolResultsTurn,
    ToolSpec,
)
from friday.adapters.llm.anthropic_provider import AnthropicProvider
from friday.domain.exceptions import ProviderError, ProviderTimeoutError, ProviderUnavailableError

_REQUEST = httpx.Request("POST", "https://api.example.test/v1/messages")


def _message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
    )


class _Stub:
    def __init__(self, outcome: Message | Exception) -> None:
        self.outcome = outcome
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> Message:
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _call(outcome: Message | Exception, **overrides: Any) -> tuple[Any, _Stub]:
    provider = AnthropicProvider("test-key", "test-model", max_retries=0)
    stub = _Stub(outcome)
    provider._client = SimpleNamespace(messages=stub)  # type: ignore[assignment]
    params: dict[str, Any] = {
        "system": "sys",
        "messages": [ChatMessage("user", "hi")],
        "max_output_tokens": 50,
        "timeout_seconds": 5.0,
    }
    params.update(overrides)
    return asyncio.run(provider.complete(**params)), stub


_TOOL = ToolSpec("web_search", "Search.", {"type": "object", "properties": {}})


def test_plain_text_request_sends_no_tool_parameters() -> None:
    result, stub = _call(_message([{"type": "text", "text": "hello"}]))
    assert stub.kwargs["tools"] is omit and stub.kwargs["tool_choice"] is omit
    assert stub.kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert (result.text, result.input_tokens, result.output_tokens) == ("hello", 7, 3)
    assert result.tool_calls == () and result.stop_reason == "end_turn"


def test_tools_are_declared_and_choice_none_is_forwarded() -> None:
    _, auto = _call(_message([{"type": "text", "text": "x"}]), tools=[_TOOL])
    assert auto.kwargs["tools"] == [
        {"name": "web_search", "description": "Search.", "input_schema": _TOOL.input_schema}
    ]
    assert auto.kwargs["tool_choice"] is omit
    _, none = _call(_message([{"type": "text", "text": "x"}]), tools=[_TOOL], tool_choice="none")
    assert none.kwargs["tool_choice"] == {"type": "none"}


def test_tool_turns_map_to_content_blocks() -> None:
    history = [
        ChatMessage("user", "q"),
        AssistantToolTurn("looking", (ToolCall("t1", "web_search", {"query": "x"}),)),
        ToolResultsTurn((ToolResult("t1", "data", False), ToolResult("t2", "nope", True))),
    ]
    _, stub = _call(_message([{"type": "text", "text": "done"}]), messages=history, tools=[_TOOL])
    assert stub.kwargs["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "looking"},
            {"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "x"}},
        ],
    }
    assert stub.kwargs["messages"][2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "data", "is_error": False},
            {"type": "tool_result", "tool_use_id": "t2", "content": "nope", "is_error": True},
        ],
    }


def test_tool_use_response_is_parsed() -> None:
    message = _message(
        [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "toolu_1", "name": "web_search", "input": {"query": "q"}},
        ],
        stop_reason="tool_use",
    )
    result, _ = _call(message, tools=[_TOOL])
    assert result.text == "let me check" and result.stop_reason == "tool_use"
    assert result.tool_calls == (ToolCall("toolu_1", "web_search", {"query": "q"}),)


def test_tool_call_truncated_by_output_limit_is_an_error() -> None:
    message = _message(
        [{"type": "tool_use", "id": "t", "name": "web_search", "input": {}}], "max_tokens"
    )
    with pytest.raises(ProviderError):
        _call(message, tools=[_TOOL])


def test_empty_response_is_an_error() -> None:
    with pytest.raises(ProviderError):
        _call(_message([{"type": "text", "text": "  "}]))


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    return cls("boom", response=httpx.Response(status, request=_REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (anthropic.APITimeoutError(request=_REQUEST), ProviderTimeoutError),
        (anthropic.APIConnectionError(request=_REQUEST), ProviderUnavailableError),
        (_status_error(anthropic.RateLimitError, 429), ProviderUnavailableError),
        (_status_error(anthropic.InternalServerError, 500), ProviderUnavailableError),
        (_status_error(anthropic.APIStatusError, 529), ProviderUnavailableError),
        (_status_error(anthropic.BadRequestError, 400), ProviderError),
        (_status_error(anthropic.AuthenticationError, 401), ProviderError),
    ],
)
def test_sdk_errors_are_translated(error: Exception, expected: type[Exception]) -> None:
    with pytest.raises(expected) as caught:
        _call(error)
    assert type(caught.value) is expected
    assert "boom" not in str(caught.value)
