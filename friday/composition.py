"""Shared construction of runtime dependencies for the API and the worker."""

from functools import lru_cache
from typing import Any

from friday.adapters.fetch.safe_fetcher import SafeFetcher
from friday.adapters.llm import LLMProvider, LLMResult
from friday.adapters.llm.anthropic_provider import build_anthropic_provider
from friday.adapters.search.base import SearchProvider
from friday.adapters.search.tavily import TavilySearchProvider
from friday.adapters.speech.base import SynthesizedAudio, Transcript
from friday.adapters.speech.openai_compatible import OpenAICompatibleSpeech
from friday.core.config import Settings
from friday.core.logging_config import get_logger
from friday.domain.exceptions import ProviderUnavailableError
from friday.orchestration.tool_registry import ToolRegistry
from friday.tools.registry import build_registry

logger = get_logger(__name__)

_registry_cache: dict[tuple[object, ...], ToolRegistry] = {}


class LazyLLMProvider:
    """Defers provider construction to first use, so request validation runs before any
    configuration error is reported to the caller."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, **kwargs: Any) -> LLMResult:
        return await build_llm(self._settings).complete(**kwargs)


class LazySpeech:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def transcribe(
        self, audio: bytes, audio_format: str, timeout_seconds: float
    ) -> Transcript:
        return await get_speech(self._settings).transcribe(audio, audio_format, timeout_seconds)

    async def synthesize(self, text: str, timeout_seconds: float) -> SynthesizedAudio:
        return await get_speech(self._settings).synthesize(text, timeout_seconds)


def build_llm(settings: Settings) -> LLMProvider:
    if not settings.anthropic_key_configured or settings.anthropic_api_key is None:
        logger.error("ANTHROPIC_API_KEY is not configured")
        raise ProviderUnavailableError
    return build_anthropic_provider(
        settings.anthropic_api_key.get_secret_value(), settings.llm_model
    )


@lru_cache
def _tavily(api_key: str) -> TavilySearchProvider:
    return TavilySearchProvider(api_key)


def get_search_provider(settings: Settings) -> SearchProvider:
    if not settings.search_key_configured or settings.search_api_key is None:
        logger.error("SEARCH_API_KEY is not configured")
        raise ProviderUnavailableError
    return _tavily(settings.search_api_key.get_secret_value())


@lru_cache
def get_fetcher() -> SafeFetcher:
    return SafeFetcher()


def get_registry(settings: Settings) -> ToolRegistry:
    """One registry (and HTTP client) per distinct tool configuration."""
    key = (
        settings.search_provider,
        settings.search_api_key.get_secret_value() if settings.search_api_key else "",
        settings.tool_timeout_seconds,
        settings.search_max_results,
        settings.max_tool_calls_per_ask,
        settings.search_daily_limit,
    )
    if key not in _registry_cache:
        provider = get_search_provider(settings) if settings.search_key_configured else None
        _registry_cache[key] = build_registry(settings, provider)
    return _registry_cache[key]


@lru_cache
def _speech(
    api_key: str, base_url: str, stt_model: str, tts_model: str, voice: str
) -> OpenAICompatibleSpeech:
    return OpenAICompatibleSpeech(
        api_key=api_key, base_url=base_url, stt_model=stt_model, tts_model=tts_model, voice=voice
    )


def get_speech(settings: Settings) -> OpenAICompatibleSpeech:
    """One shared HTTP client for transcription and synthesis."""
    if not settings.speech_configured or settings.speech_api_key is None:
        logger.error("SPEECH_API_KEY is not configured")
        raise ProviderUnavailableError
    return _speech(
        settings.speech_api_key.get_secret_value(),
        settings.speech_base_url,
        settings.stt_model,
        settings.tts_model,
        settings.tts_voice,
    )
