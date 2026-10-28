import asyncio
import json
from typing import Any

import httpx
import pytest

from friday.adapters.speech.openai_compatible import MAX_SYNTHESIZED_BYTES, OpenAICompatibleSpeech
from friday.composition import get_speech
from friday.core.config import Settings
from friday.domain.exceptions import ProviderError, ProviderTimeoutError, ProviderUnavailableError

KEY = "sk-test-secret-key-0123456789"
BASE = "https://speech.example.com/v1"


def speech(handler: Any, **overrides: Any) -> OpenAICompatibleSpeech:
    params: dict[str, Any] = {
        "api_key": KEY,
        "base_url": BASE + "/",
        "stt_model": "whisper-1",
        "tts_model": "gpt-4o-mini-tts",
        "voice": "alloy",
    }
    params.update(overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return OpenAICompatibleSpeech(client=client, **params)


def stt(
    handler: Any, audio: bytes = b"RIFFxxxxWAVEdata", fmt: str = "wav", **overrides: Any
) -> Any:
    return asyncio.run(speech(handler, **overrides).transcribe(audio, fmt, 5.0))


def tts(handler: Any, text: str = "hello there") -> Any:
    return asyncio.run(speech(handler).synthesize(text, 5.0))


def ok_audio(data: bytes = b"ID3audio") -> httpx.Response:
    return httpx.Response(200, content=data, headers={"content-type": "audio/mpeg"})


# transcription


def test_transcription_request_is_a_bearer_authenticated_multipart_upload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " what time is it "})

    result = stt(handler, b"RIFF....WAVEfmt ", "wav", language="en")
    assert result.text == " what time is it "
    [request] = seen
    assert request.method == "POST" and str(request.url) == f"{BASE}/audio/transcriptions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content.decode("latin-1")
    assert 'name="model"' in body and "whisper-1" in body
    assert 'name="response_format"' in body and "json" in body
    assert 'name="language"' in body and 'name="file"; filename="audio.wav"' in body
    assert "Content-Type: audio/wav" in body and "RIFF....WAVEfmt " in body


def test_language_is_only_sent_when_configured_and_formats_get_matching_types() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "x"})

    stt(handler, b"\x00\x00\x00\x20ftypM4A ", "m4a")
    body = seen[0].content.decode("latin-1")
    assert 'name="language"' not in body
    assert 'filename="audio.m4a"' in body and "Content-Type: audio/mp4" in body


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ProviderUnavailableError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
        (401, ProviderError),
        (403, ProviderError),
        (400, ProviderError),
        (413, ProviderError),
        (302, None),  # redirects are never followed; a 3xx carries no transcript
    ],
)
def test_transcription_statuses_map_to_domain_errors_without_leaking_bodies(
    status: int, expected: type[Exception] | None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, text=f"internal detail {KEY}", headers={"location": "http://169.254.169.254/"}
        )

    with pytest.raises(Exception) as caught:
        stt(handler)
    assert isinstance(caught.value, expected or ProviderError)
    assert type(caught.value) is (expected or ProviderError)
    assert KEY not in str(caught.value) and "internal detail" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [{"text": 5}, {"nope": "x"}, [], "plain string", None],
)
def test_malformed_transcription_bodies_are_provider_errors(payload: Any) -> None:
    with pytest.raises(ProviderError):
        stt(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError):
        stt(lambda request: httpx.Response(200, content=b"not json"))


def test_transport_failures_map_to_timeout_or_unavailable() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderTimeoutError):
        stt(slow)
    with pytest.raises(ProviderUnavailableError):
        stt(refused)


# synthesis


def test_synthesis_request_and_response() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok_audio(b"ID3spoken")

    result = tts(handler, "Four.")
    assert (result.data, result.audio_format) == (b"ID3spoken", "mp3")
    [request] = seen
    assert str(request.url) == f"{BASE}/audio/speech" and request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "model": "gpt-4o-mini-tts",
        "input": "Four.",
        "voice": "alloy",
        "response_format": "mp3",
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(429, text="slow down"), ProviderUnavailableError),
        (httpx.Response(502, text="bad gateway"), ProviderUnavailableError),
        (httpx.Response(401, text="bad key"), ProviderError),
        (httpx.Response(400, text="bad input"), ProviderError),
        (
            httpx.Response(200, content=b"{}", headers={"content-type": "application/json"}),
            ProviderError,
        ),
        (
            httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
            ProviderError,
        ),
        (httpx.Response(200, content=b"", headers={"content-type": "audio/mpeg"}), ProviderError),
    ],
)
def test_synthesis_failures_are_classified(
    response: httpx.Response, expected: type[Exception]
) -> None:
    with pytest.raises(Exception) as caught:
        tts(lambda request: response)
    assert type(caught.value) is expected
    assert KEY not in str(caught.value) and "bad key" not in str(caught.value)


def test_oversized_synthesized_audio_is_rejected_while_streaming() -> None:
    big = b"a" * (MAX_SYNTHESIZED_BYTES + 1)
    with pytest.raises(ProviderError):
        tts(lambda request: ok_audio(big))


def test_synthesis_timeouts_and_connection_errors() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ProviderTimeoutError):
        tts(slow)
    with pytest.raises(ProviderUnavailableError):
        tts(refused)


def test_the_provider_is_unavailable_until_a_key_is_configured() -> None:
    with pytest.raises(ProviderUnavailableError):
        get_speech(Settings(_env_file=None))
    configured = Settings(_env_file=None, speech_api_key="k")  # type: ignore[arg-type]
    assert get_speech(configured) is get_speech(configured)  # one shared client
