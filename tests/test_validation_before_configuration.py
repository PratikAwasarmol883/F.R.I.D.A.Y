"""A caller's mistake must be reported before server configuration problems."""

from typing import Any

import pytest

from .conftest import Seeded
from .helpers import audit_rows
from .test_voice_foundations import wav_bytes

ASK = "/api/v1/assistant/ask"
VOICE = "/api/v1/assistant/voice"


@pytest.fixture
def unconfigured(make_client: Any) -> Any:
    return make_client(None)  # no LLM, no speech, no search: nothing is configured


def test_invalid_ask_bodies_are_validation_errors_not_configuration_errors(
    unconfigured: Any, alice: Seeded
) -> None:
    for payload in ({}, {"message": ""}, {"message": "hi", "extra": 1}):
        response = unconfigured.post(ASK, json=payload, headers=alice.headers)
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    valid = unconfigured.post(ASK, json={"message": "hi"}, headers=alice.headers)
    assert valid.status_code == 503 and valid.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_invalid_voice_requests_are_client_errors_not_configuration_errors(
    unconfigured: Any, alice: Seeded
) -> None:
    junk = unconfigured.post(
        VOICE,
        files={"audio": ("a.wav", b"<html>not audio</html>" * 3, "audio/wav")},
        headers=alice.headers,
    )
    assert junk.status_code == 415 and junk.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"
    missing = unconfigured.post(VOICE, data={"speak": "true"}, headers=alice.headers)
    assert missing.status_code == 422
    valid = unconfigured.post(
        VOICE, files={"audio": ("a.wav", wav_bytes(), "audio/wav")}, headers=alice.headers
    )
    assert valid.status_code == 503 and valid.json()["code"] == "PROVIDER_UNAVAILABLE"


def test_an_unconfigured_provider_is_audited_as_a_failure_not_ignored(
    unconfigured: Any, alice: Seeded, session_factory: Any
) -> None:
    unconfigured.post(ASK, json={"message": "hi"}, headers=alice.headers)
    assert [a.outcome for a in audit_rows(session_factory, "assistant.ask")] == ["failure"]
