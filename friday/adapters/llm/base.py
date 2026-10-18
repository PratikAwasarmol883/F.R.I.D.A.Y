from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> LLMResult: ...
