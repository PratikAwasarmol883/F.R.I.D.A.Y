"""Speech-to-text and text-to-speech over an OpenAI-compatible HTTP API."""

import httpx

from friday.adapters.speech.base import SynthesizedAudio, Transcript
from friday.core.logging_config import get_logger
from friday.domain.exceptions import ProviderError, ProviderTimeoutError, ProviderUnavailableError

logger = get_logger(__name__)

MAX_SYNTHESIZED_BYTES = 10_000_000
_MIME = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
}


def _check_status(status: int) -> None:
    """Translate an HTTP status without ever surfacing the provider's response body."""
    if status < 400:
        return
    logger.warning("Speech provider returned status %s", status)
    if status == 429 or status >= 500:
        raise ProviderUnavailableError
    raise ProviderError  # includes 401/403: a misconfigured key is not retryable


class OpenAICompatibleSpeech:
    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        stt_model: str,
        tts_model: str,
        voice: str,
        language: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._stt_model = stt_model
        self._tts_model = tts_model
        self._voice = voice
        self._language = language
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self, audio: bytes, audio_format: str, timeout_seconds: float
    ) -> Transcript:
        data = {"model": self._stt_model, "response_format": "json"}
        if self._language:
            data["language"] = self._language
        files = {"file": (f"audio.{audio_format}", audio, _MIME.get(audio_format, "audio/wav"))}
        try:
            response = await self._client.post(
                f"{self._base_url}/audio/transcriptions",
                headers=self._headers,
                data=data,
                files=files,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError from exc
        _check_status(response.status_code)
        try:
            text = response.json()["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("malformed transcription response") from exc
        if not isinstance(text, str):
            raise ProviderError("malformed transcription response")
        return Transcript(text=text)

    async def synthesize(self, text: str, timeout_seconds: float) -> SynthesizedAudio:
        body = {
            "model": self._tts_model,
            "input": text,
            "voice": self._voice,
            "response_format": "mp3",
        }
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/audio/speech",
                headers=self._headers,
                json=body,
                timeout=timeout_seconds,
            ) as response:
                _check_status(response.status_code)
                if not response.headers.get("content-type", "").lower().startswith("audio/"):
                    raise ProviderError("unexpected speech response type")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_SYNTHESIZED_BYTES:
                        raise ProviderError("synthesized audio too large")
                    chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError from exc
        if not total:
            raise ProviderError("empty synthesized audio")
        return SynthesizedAudio(data=b"".join(chunks), audio_format="mp3")
