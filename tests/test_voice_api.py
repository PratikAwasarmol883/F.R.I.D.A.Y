import base64
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from friday.adapters.llm.base import ChatMessage
from friday.adapters.llm.fake import FakeLLMProvider
from friday.adapters.speech.fake import FakeSpeech
from friday.domain.exceptions import (
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from friday.services.assistant_service import VOICE_SUFFIX

from .conftest import Seeded, SessionFactory, _Collector
from .helpers import audit_rows, message_rows, usage_rows
from .test_voice_foundations import wav_bytes

VOICE = "/api/v1/assistant/voice"
WAV = wav_bytes()


def send(
    client: TestClient,
    who: Seeded | None,
    audio: bytes = WAV,
    *,
    content_type: str = "audio/wav",
    **form: Any,
) -> Any:
    headers = who.headers if who else {}
    data = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in form.items()
    }
    return client.post(
        VOICE, files={"audio": ("recording.wav", audio, content_type)}, data=data, headers=headers
    )


def voice_client(
    make_client: Any,
    speech: FakeSpeech | None = None,
    llm: FakeLLMProvider | None = None,
    **overrides: Any,
):  # type: ignore[no-untyped-def]
    speech = speech or FakeSpeech()
    llm = llm or FakeLLMProvider()
    return make_client(llm, None, speech, **overrides), speech, llm


# happy path


def test_a_spoken_question_returns_text_and_speech_and_stores_only_text(
    make_client: Any, alice: Seeded, session_factory: SessionFactory
) -> None:
    client, speech, llm = voice_client(make_client)
    response = send(client, alice)
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "what is two plus two" and body["answer"] == "fake answer"
    assert body["audio"]["format"] == "mp3"
    assert base64.b64decode(body["audio"]["base64"]) == speech.audio
    assert body["speech_error"] is None and body["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
    }

    assert speech.transcriptions == [(len(WAV), "wav")] and speech.syntheses == ["fake answer"]
    assert llm.requests[0].system.endswith(VOICE_SUFFIX)
    user_msg, reply = message_rows(session_factory)
    assert (user_msg.content, reply.content) == ("what is two plus two", "fake answer")
    assert [a.outcome for a in audit_rows(session_factory, "assistant.voice.attempt")] == [
        "success"
    ]
    assert [a.outcome for a in audit_rows(session_factory, "assistant.voice")] == ["success"]
    assert len(usage_rows(session_factory)) == 1


def test_text_asks_do_not_use_the_voice_style(
    client: TestClient, llm: FakeLLMProvider, alice: Seeded
) -> None:
    client.post("/api/v1/assistant/ask", json={"message": "hi"}, headers=alice.headers)
    assert VOICE_SUFFIX not in llm.requests[0].system


def test_speak_false_skips_synthesis(make_client: Any, alice: Seeded) -> None:
    client, speech, _ = voice_client(make_client)
    body = send(client, alice, speak=False).json()
    assert body["audio"] is None and body["speech_error"] is None and speech.syntheses == []


def test_the_spoken_text_is_plain_while_the_answer_keeps_its_formatting(
    make_client: Any, alice: Seeded
) -> None:
    llm = FakeLLMProvider(
        answer="**Bold** answer, see [docs](https://docs.example/x) and https://evil.example/y"
    )
    client, speech, _ = voice_client(make_client, llm=llm)
    body = send(client, alice).json()
    assert "**Bold**" in body["answer"] and "https://docs.example/x" in body["answer"]
    assert speech.syntheses == ["Bold answer, see docs and a link"]


def test_conversations_continue_across_voice_turns_and_are_owner_checked(
    make_client: Any, alice: Seeded, bob: Seeded
) -> None:
    client, speech, llm = voice_client(make_client)
    first = send(client, alice).json()
    send(client, alice, conversation_id=first["conversation_id"])
    assert llm.calls[1] == [
        ChatMessage("user", "what is two plus two"),
        ChatMessage("assistant", "fake answer"),
        ChatMessage("user", "what is two plus two"),
    ]
    before = len(speech.transcriptions)
    foreign = send(client, bob, conversation_id=first["conversation_id"])
    assert foreign.status_code == 404 and len(speech.transcriptions) == before  # no paid call


# input validation


def test_authentication_comes_first(make_client: Any, alice: Seeded) -> None:
    client, speech, _ = voice_client(make_client)
    assert send(client, None).status_code == 401
    assert send(make_client(FakeLLMProvider()), alice).status_code == 503  # speech not configured
    assert speech.transcriptions == []


@pytest.mark.parametrize(
    ("audio", "declared"),
    [
        (b"", "audio/wav"),
        (b"<html><body>not audio at all</body></html>", "audio/wav"),
        (b"MZ" + b"\x00" * 64, "audio/mpeg"),
        (b"%PDF-1.7 " + b"\x00" * 64, "application/pdf"),
        (b"plain text recording" * 4, "text/plain"),
    ],
)
def test_only_real_audio_is_accepted_whatever_the_declared_type(
    make_client: Any, alice: Seeded, audio: bytes, declared: str
) -> None:
    client, speech, _ = voice_client(make_client)
    response = send(client, alice, audio, content_type=declared)
    assert response.status_code == 415 and response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert speech.transcriptions == []


def test_malformed_forms_are_validation_errors(make_client: Any, alice: Seeded) -> None:
    client, speech, _ = voice_client(make_client)
    assert client.post(VOICE, data={"speak": "true"}, headers=alice.headers).status_code == 422
    assert send(client, alice, conversation_id="not-a-uuid").status_code == 422
    assert send(client, alice, speak="maybe").status_code == 422
    assert speech.transcriptions == []


# size limits


def test_uploads_over_the_limit_are_rejected_before_any_paid_call(
    make_client: Any, alice: Seeded
) -> None:
    client, speech, _ = voice_client(make_client, voice_max_upload_bytes=1_000)
    over = WAV + b"\x00" * 2_000  # passes the transport cap, fails the application cap
    response = send(client, alice, over)
    assert response.status_code == 413 and response.json()["code"] == "PAYLOAD_TOO_LARGE"
    huge = send(client, alice, WAV + b"\x00" * 200_000)  # exceeds the per-path transport cap
    assert huge.status_code == 413 and huge.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert speech.transcriptions == []


def test_chunked_uploads_without_a_length_are_bounded_while_streaming(
    make_client: Any, alice: Seeded
) -> None:
    client, speech, _ = voice_client(make_client, voice_max_upload_bytes=1_000)
    boundary = "xBOUNDARYx"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="a.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode()

    def chunks() -> Any:
        yield head
        for _ in range(100):
            yield b"\x00" * 4_000  # 400 KB in total, never declared up front
        yield f"\r\n--{boundary}--\r\n".encode()

    headers = {**alice.headers, "content-type": f"multipart/form-data; boundary={boundary}"}
    response = client.post(VOICE, content=chunks(), headers=headers)
    assert response.status_code == 413 and response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert speech.transcriptions == []


def test_streaming_limits_apply_to_ordinary_json_endpoints_too(
    client: TestClient, alice: Seeded
) -> None:
    def chunks() -> Any:
        yield b'{"message": "'
        for _ in range(40):
            yield b"x" * 4_000
        yield b'"}'

    headers = {**alice.headers, "content-type": "application/json"}
    response = client.post("/api/v1/assistant/ask", content=chunks(), headers=headers)
    assert response.status_code == 413 and response.json()["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


# transcription failures


def test_blank_transcripts_are_no_speech_and_store_nothing(
    make_client: Any, alice: Seeded, session_factory: SessionFactory
) -> None:
    client, _, llm = voice_client(make_client, FakeSpeech(transcript="   \n"))
    response = send(client, alice)
    assert response.status_code == 422 and response.json()["code"] == "NO_SPEECH_DETECTED"
    assert llm.calls == [] and message_rows(session_factory) == []
    assert [a.outcome for a in audit_rows(session_factory, "assistant.voice")] == ["failure"]


def test_overlong_transcripts_are_rejected(make_client: Any, alice: Seeded) -> None:
    client, _, llm = voice_client(make_client, FakeSpeech(transcript="word " * 2_000))
    assert send(client, alice).status_code == 413 and llm.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ProviderTimeoutError("secret-detail"), 504, "PROVIDER_TIMEOUT"),
        (ProviderUnavailableError("secret-detail"), 503, "PROVIDER_UNAVAILABLE"),
        (ProviderError("secret-detail"), 502, "PROVIDER_ERROR"),
    ],
)
def test_transcription_provider_failures_are_safe_and_store_nothing(
    make_client: Any,
    alice: Seeded,
    session_factory: SessionFactory,
    error: Exception,
    status: int,
    code: str,
) -> None:
    client, _, llm = voice_client(make_client, FakeSpeech(stt_error=error))
    response = send(client, alice)
    assert (response.status_code, response.json()["code"]) == (status, code)
    assert "secret-detail" not in response.text and llm.calls == []
    assert message_rows(session_factory) == []
    assert [a.outcome for a in audit_rows(session_factory, "assistant.voice")] == ["failure"]


# synthesis failure degrades to text


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderTimeoutError(), "provider_timeout"),
        (ProviderUnavailableError(), "provider_unavailable"),
        (ProviderError(), "provider_error"),
    ],
)
def test_a_speech_failure_still_returns_the_stored_answer_as_text(
    make_client: Any, alice: Seeded, session_factory: SessionFactory, error: Exception, code: str
) -> None:
    client, _, _ = voice_client(make_client, FakeSpeech(tts_error=error))
    response = send(client, alice)
    body = response.json()
    assert response.status_code == 200 and body["answer"] == "fake answer"
    assert body["audio"] is None and body["speech_error"] == code
    assert len(message_rows(session_factory)) == 2
    assert [a.outcome for a in audit_rows(session_factory, "assistant.voice")] == ["success"]


# limits


def test_the_daily_voice_limit_counts_every_paid_attempt(
    make_client: Any, alice: Seeded, bob: Seeded
) -> None:
    speech = FakeSpeech()
    client, _, _ = voice_client(make_client, speech, voice_daily_limit=2)
    assert send(client, alice).status_code == 200
    speech.stt_error = ProviderUnavailableError()
    assert send(client, alice).status_code == 503  # a failed attempt still used quota
    speech.stt_error = None
    third = send(client, alice)
    assert third.status_code == 429 and third.json()["code"] == "VOICE_LIMIT"
    assert len(speech.transcriptions) == 2
    assert send(client, bob).status_code == 200  # per user
    assert send(client, alice, b"junk-not-audio" * 4, content_type="audio/wav").status_code == 415


def test_the_token_budget_still_applies_to_voice(make_client: Any, alice: Seeded) -> None:
    client, _, _ = voice_client(make_client, daily_token_budget=15)
    assert send(client, alice).status_code == 200
    blocked = send(client, alice)
    assert blocked.status_code == 429 and blocked.json()["code"] == "BUDGET_EXCEEDED"


# privacy


def test_logs_and_audit_never_contain_audio_transcripts_or_answers(
    make_client: Any, alice: Seeded, session_factory: SessionFactory
) -> None:
    speech = FakeSpeech(transcript="PRIVATETRANSCRIPT", audio=b"ID3-PRIVATEAUDIO")
    client, _, _ = voice_client(make_client, speech, llm=FakeLLMProvider(answer="PRIVATEANSWER"))
    collector = _Collector()
    logging.getLogger().addHandler(collector)
    try:
        response = send(client, alice)
    finally:
        logging.getLogger().removeHandler(collector)
    assert response.status_code == 200
    joined = "\n".join(collector.lines)
    assert collector.lines
    for secret in ("PRIVATETRANSCRIPT", "PRIVATEANSWER", "PRIVATEAUDIO", alice.token):
        assert secret not in joined
    rows = repr([vars(a) for a in audit_rows(session_factory, "assistant.voice")])
    assert "PRIVATE" not in rows
