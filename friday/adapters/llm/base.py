from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ChatMessage:
    """A plain-text turn (role is "user" or "assistant")."""

    role: str
    content: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any  # untrusted model output; validated by policy, never by the adapter


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class AssistantToolTurn:
    """An assistant turn that requested tool calls (optionally with preamble text)."""

    text: str
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class ToolResultsTurn:
    results: tuple[ToolResult, ...]


LLMMessage = ChatMessage | AssistantToolTurn | ToolResultsTurn


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = "end_turn"


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        max_output_tokens: int,
        timeout_seconds: float,
        tools: Sequence[ToolSpec] = (),
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> LLMResult: ...
