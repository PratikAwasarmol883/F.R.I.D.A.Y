from collections.abc import Sequence
from functools import lru_cache
from typing import Literal

import anthropic
from anthropic import Omit, omit
from anthropic.types import (
    MessageParam,
    TextBlockParam,
    ToolChoiceNoneParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)

from friday.adapters.llm.base import (
    AssistantToolTurn,
    ChatMessage,
    LLMMessage,
    LLMResult,
    ToolCall,
    ToolResultsTurn,
    ToolSpec,
)
from friday.core.logging_config import get_logger
from friday.domain.exceptions import ProviderError, ProviderTimeoutError, ProviderUnavailableError

logger = get_logger(__name__)


def _to_api_message(message: LLMMessage) -> MessageParam:
    if isinstance(message, ChatMessage):
        role: Literal["user", "assistant"] = "user" if message.role == "user" else "assistant"
        return {"role": role, "content": message.content}
    if isinstance(message, AssistantToolTurn):
        blocks: list[TextBlockParam | ToolUseBlockParam] = []
        if message.text:
            blocks.append(TextBlockParam(type="text", text=message.text))
        blocks.extend(
            ToolUseBlockParam(type="tool_use", id=call.id, name=call.name, input=call.arguments)
            for call in message.tool_calls
        )
        return {"role": "assistant", "content": blocks}
    assert isinstance(message, ToolResultsTurn)
    return {
        "role": "user",
        "content": [
            ToolResultBlockParam(
                type="tool_result",
                tool_use_id=result.tool_call_id,
                content=result.content,
                is_error=result.is_error,
            )
            for result in message.results
        ],
    }


class AnthropicProvider:
    """Translates SDK types and errors; nothing Anthropic-specific leaks past this module."""

    provider_name = "anthropic"

    def __init__(self, api_key: str, model: str, max_retries: int = 2) -> None:
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries)

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
        api_messages = [_to_api_message(m) for m in messages]
        tool_params: list[ToolParam] | Omit = omit
        choice: ToolChoiceNoneParam | Omit = omit
        if tools:
            tool_params = [
                ToolParam(name=t.name, description=t.description, input_schema=t.input_schema)
                for t in tools
            ]
            if tool_choice == "none":
                choice = ToolChoiceNoneParam(type="none")
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_output_tokens,
                system=system,
                messages=api_messages,
                timeout=timeout_seconds,
                tools=tool_params,
                tool_choice=choice,
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise ProviderUnavailableError from exc
        except anthropic.APIStatusError as exc:
            logger.warning("Provider returned status %s", exc.status_code)
            if exc.status_code >= 500:
                raise ProviderUnavailableError from exc
            raise ProviderError from exc
        except anthropic.APIError as exc:
            raise ProviderError from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        tool_calls = tuple(
            ToolCall(id=block.id, name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        )
        if response.stop_reason == "max_tokens" and tool_calls:
            raise ProviderError("Tool call truncated by the output limit")
        if not text.strip() and not tool_calls:
            raise ProviderError("Provider returned no content")
        return LLMResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self._model,
            provider=self.provider_name,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "unknown",
        )


@lru_cache
def build_anthropic_provider(api_key: str, model: str) -> AnthropicProvider:
    return AnthropicProvider(api_key=api_key, model=model)
