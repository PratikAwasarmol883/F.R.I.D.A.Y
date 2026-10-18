from functools import lru_cache

import anthropic
from anthropic.types import MessageParam

from friday.adapters.llm.base import ChatMessage, LLMResult
from friday.core.logging_config import get_logger
from friday.domain.exceptions import ProviderError, ProviderTimeoutError, ProviderUnavailableError

logger = get_logger(__name__)


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
        messages: list[ChatMessage],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> LLMResult:
        api_messages: list[MessageParam] = [
            {"role": "user" if m.role == "user" else "assistant", "content": m.content}
            for m in messages
        ]
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_output_tokens,
                system=system,
                messages=api_messages,
                timeout=timeout_seconds,
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
        if not text.strip():
            raise ProviderError("Provider returned no text")
        return LLMResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self._model,
            provider=self.provider_name,
        )


@lru_cache
def build_anthropic_provider(api_key: str, model: str) -> AnthropicProvider:
    return AnthropicProvider(api_key=api_key, model=model)
