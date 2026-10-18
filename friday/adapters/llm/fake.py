from friday.adapters.llm.base import ChatMessage, LLMResult


class FakeLLMProvider:
    """Deterministic provider for tests; records calls and can be told to fail."""

    def __init__(
        self,
        answer: str = "fake answer",
        input_tokens: int = 10,
        output_tokens: int = 5,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> LLMResult:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return LLMResult(
            text=self.answer,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            model="fake-model",
            provider="fake",
        )
