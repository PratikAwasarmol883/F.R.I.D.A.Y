from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from friday.adapters.llm.base import LLMMessage, LLMResult, ToolSpec


@dataclass(frozen=True)
class FakeRequest:
    system: str
    messages: tuple[LLMMessage, ...]
    tools: tuple[ToolSpec, ...]
    tool_choice: str


class FakeLLMProvider:
    """Deterministic provider for tests.

    By default every call returns `answer`. Pass `script` (results or exceptions, consumed in
    order) to drive multi-step tool conversations.
    """

    def __init__(
        self,
        answer: str = "fake answer",
        input_tokens: int = 10,
        output_tokens: int = 5,
        error: Exception | None = None,
        script: Sequence[LLMResult | Exception] = (),
    ) -> None:
        self.answer = answer
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.error = error
        self._script = list(script)
        self.calls: list[list[LLMMessage]] = []
        self.requests: list[FakeRequest] = []

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[LLMMessage],
        max_output_tokens: int,
        timeout_seconds: float,
        tools: Sequence[ToolSpec] = (),
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> LLMResult:
        self.calls.append(list(messages))
        self.requests.append(FakeRequest(system, tuple(messages), tuple(tools), tool_choice))
        if self.error is not None:
            raise self.error
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return LLMResult(
            text=self.answer,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model="fake-model",
            provider="fake",
        )
