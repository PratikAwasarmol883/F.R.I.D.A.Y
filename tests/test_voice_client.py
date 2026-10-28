import base64
import logging
import wave
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from friday_client.api import ApiError, FridayApi, VoiceReply, validate_base_url
from friday_client.audio import (
    AudioDeviceError,
    Decision,
    EndpointDetector,
    NoSpeechCaptured,
    PlaybackError,
    encode_wav,
    rms,
)
from friday_client.core import (
    DEFAULT_ERROR,
    ERROR_MESSAGES,
    Outcome,
    State,
    TriggerEvent,
    VoiceClient,
)
from friday_client.tokens import ChainTokenStore, EnvTokenStore, KeyringTokenStore

WAV = encode_wav(b"\x10\x00" * 160)


def reply(**overrides: Any) -> VoiceReply:
    fields: dict[str, Any] = {
        "transcript": "hello",
        "answer": "Hi there.",
        "conversation_id": "conv-1",
        "audio": b"ID3-audio",
        "audio_format": "mp3",
        "speech_error": None,
    }
    fields.update(overrides)
    return VoiceReply(**fields)


class Rig:
    """Fakes for every device plus one shared event log for ordering assertions."""

    def __init__(self, **kwargs: Any) -> None:
        self.log: list[str] = []
        self.states: list[tuple[State, str]] = []
        self.said: list[str] = []
        self.played: list[tuple[bytes, str]] = []
        self.api_calls: list[tuple[bytes, str | None]] = []
        self.trigger_muted_flags: list[bool] = []
        self.events: list[TriggerEvent] = []
        self.mic_result: Any = WAV
        self.api_result: Any = reply()
        self.play_error: Exception | None = None
        self.say_error: Exception | None = None
        self.on_mic: Callable[[], None] | None = None
        self.on_play: Callable[[], None] | None = None
        self.on_say: Callable[[], None] | None = None
        self.on_api: Callable[[], None] | None = None
        self.stops = {"player": 0, "local": 0}
        self.now = 1000.0
        self.client = VoiceClient(
            mic=self,  # type: ignore[arg-type]
            trigger=self,  # type: ignore[arg-type]
            player=_Player(self),
            local_speaker=_Local(self),
            api=_Api(self),
            indicator=self._indicate,
            clock=lambda: self.now,
            **kwargs,
        )

    # indicator
    def _indicate(self, state: State, detail: str) -> None:
        self.states.append((state, detail))
        self.log.append(f"indicator:{state.value}")

    # microphone
    def record_utterance(self) -> bytes:
        self.log.append("mic:record")
        if self.on_mic:
            self.on_mic()
        if isinstance(self.mic_result, Exception):
            raise self.mic_result
        return self.mic_result

    # trigger
    def wait(self, timeout: float, *, muted: bool) -> TriggerEvent:
        self.trigger_muted_flags.append(muted)
        self.log.append(f"trigger:wait(muted={muted})")
        return self.events.pop(0) if self.events else TriggerEvent.NONE

    def turn(self) -> Outcome:
        self.events.append(TriggerEvent.TALK)
        return self.client.run_once()

    def state_names(self) -> list[str]:
        return [state.value for state, _ in self.states]


class _Player:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def play(self, audio: bytes, audio_format: str) -> None:
        self.rig.log.append("player:play")
        if self.rig.on_play:
            self.rig.on_play()
        if self.rig.play_error:
            raise self.rig.play_error
        self.rig.played.append((audio, audio_format))

    def stop(self) -> None:
        self.rig.stops["player"] += 1


class _Local:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def say(self, text: str) -> None:
        self.rig.log.append("local:say")
        if self.rig.on_say:
            self.rig.on_say()
        if self.rig.say_error:
            raise self.rig.say_error
        self.rig.said.append(text)

    def stop(self) -> None:
        self.rig.stops["local"] += 1


class _Api:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig

    def ask_voice(
        self, wav: bytes, conversation_id: str | None = None, *, speak: bool = True
    ) -> VoiceReply:
        self.rig.log.append("api:ask_voice")
        if self.rig.on_api:
            self.rig.on_api()
        self.rig.api_calls.append((wav, conversation_id))
        if isinstance(self.rig.api_result, Exception):
            raise self.rig.api_result
        return self.rig.api_result


# the normal turn


def test_a_normal_turn_goes_listening_thinking_speaking_idle() -> None:
    rig = Rig()
    assert rig.turn() is Outcome.SPOKE
    assert rig.state_names() == ["listening", "thinking", "speaking", "idle"]
    assert rig.played == [(b"ID3-audio", "mp3")] and rig.said == []
    assert rig.api_calls == [(WAV, None)]


def test_the_listening_indicator_is_shown_before_the_microphone_opens() -> None:
    rig = Rig()
    rig.turn()
    assert rig.log.index("indicator:listening") < rig.log.index("mic:record")
    assert rig.log.index("indicator:thinking") > rig.log.index(
        "mic:record"
    )  # audio is sent only after


def test_nothing_is_recorded_or_sent_without_a_trigger() -> None:
    rig = Rig()
    for _ in range(5):
        assert rig.client.run_once() is Outcome.IDLE_TICK
    assert "mic:record" not in rig.log and rig.api_calls == []


def test_the_conversation_continues_until_it_has_been_idle_too_long() -> None:
    rig = Rig(conversation_idle_seconds=60)
    rig.turn()
    rig.now += 30
    rig.turn()
    assert [call[1] for call in rig.api_calls] == [None, "conv-1"]
    rig.now += 61
    rig.turn()
    assert rig.api_calls[-1][1] is None  # a fresh conversation after the idle window


def test_quit_stops_the_client() -> None:
    rig = Rig()
    rig.events.append(TriggerEvent.QUIT)
    assert rig.client.run_once() is Outcome.QUIT
    assert rig.stops["player"] >= 1


# mute


def test_while_muted_nothing_is_captured_or_sent_and_the_state_is_visible() -> None:
    rig = Rig()
    rig.client.mute()
    rig.events += [TriggerEvent.TALK, TriggerEvent.TALK]  # even a talk request is ignored
    assert [rig.client.run_once(), rig.client.run_once()] == [Outcome.MUTED, Outcome.MUTED]
    assert "mic:record" not in rig.log and rig.api_calls == []
    assert rig.trigger_muted_flags == [True, True]  # the trigger source is told it is muted
    assert rig.state_names()[-1] == "muted" and rig.client.muted


def test_muting_stops_playback_immediately_and_unmuting_resumes() -> None:
    rig = Rig()
    rig.client.mute()
    assert rig.stops == {"player": 1, "local": 1}
    rig.events.append(TriggerEvent.TOGGLE_MUTE)  # the keyboard control reaches us even when muted
    assert rig.client.run_once() is Outcome.IDLE_TICK and not rig.client.muted
    assert rig.turn() is Outcome.SPOKE


def test_toggle_mute_from_the_keyboard() -> None:
    rig = Rig()
    rig.events.append(TriggerEvent.TOGGLE_MUTE)
    assert rig.client.run_once() is Outcome.MUTED and rig.client.muted
    rig.events.append(TriggerEvent.TOGGLE_MUTE)
    assert rig.client.run_once() is Outcome.IDLE_TICK and not rig.client.muted


def test_muting_while_recording_discards_the_audio_and_sends_nothing() -> None:
    rig = Rig()
    rig.on_mic = rig.client.mute
    assert rig.turn() is Outcome.MUTED
    assert rig.api_calls == [] and rig.played == [] and rig.said == []


def test_muting_while_waiting_for_the_server_suppresses_the_reply() -> None:
    rig = Rig()
    rig.on_api = (
        rig.client.mute
    )  # the user mutes after the audio was sent, before the reply arrives
    assert rig.turn() is Outcome.MUTED
    assert rig.played == [] and rig.said == [] and rig.state_names()[-1] == "muted"


def test_muting_during_playback_stops_it_and_the_client_stays_muted() -> None:
    rig = Rig()
    rig.on_play = rig.client.mute
    assert rig.turn() is Outcome.SPOKE
    assert rig.stops["player"] >= 1 and rig.client.muted
    assert (
        rig.state_names()[-1] == "muted" and rig.said == []
    )  # no idle flicker, no fallback speech


def test_muting_during_local_speech_stops_it_and_the_client_stays_muted() -> None:
    rig = Rig()
    rig.api_result = reply(audio=None, audio_format=None)
    rig.on_say = rig.client.mute
    assert rig.turn() is Outcome.SPOKE
    assert rig.stops["local"] >= 1 and rig.state_names()[-1] == "muted"


def test_a_failed_playback_does_not_speak_if_the_user_muted_meanwhile() -> None:
    rig = Rig()
    rig.play_error = PlaybackError()
    rig.on_play = rig.client.mute
    assert rig.turn() is Outcome.SPOKE
    assert rig.said == []


# failure paths: the loop always survives


def test_a_missing_microphone_is_reported_and_the_client_recovers() -> None:
    rig = Rig()
    rig.mic_result = AudioDeviceError("no device")
    assert rig.turn() is Outcome.ERROR
    assert rig.state_names() == ["listening", "error", "idle"]
    assert rig.said == [ERROR_MESSAGES["NO_MICROPHONE"]] and rig.api_calls == []
    rig.mic_result = WAV  # the device came back
    assert rig.turn() is Outcome.SPOKE


def test_silence_is_not_an_error_and_sends_nothing() -> None:
    rig = Rig()
    rig.mic_result = NoSpeechCaptured()
    assert rig.turn() is Outcome.NO_SPEECH and rig.api_calls == [] and rig.said == []
    assert rig.state_names()[-1] == "idle"


@pytest.mark.parametrize("code", sorted(ERROR_MESSAGES) + ["SOMETHING_NEW", "BAD_RESPONSE"])
def test_every_api_failure_shows_a_fixed_message_never_server_text(code: str) -> None:
    rig = Rig()
    rig.api_result = ApiError(code, 500)
    assert rig.turn() is Outcome.ERROR
    expected = ERROR_MESSAGES.get(code, DEFAULT_ERROR)
    assert (State.ERROR, expected) in rig.states and rig.said == [expected]
    assert rig.state_names()[-1] == "idle"
    rig.api_result = reply()
    assert rig.turn() is Outcome.SPOKE  # and the next turn works


def test_unexpected_exceptions_in_devices_and_api_do_not_escape() -> None:
    rig = Rig()
    rig.mic_result = RuntimeError("portaudio exploded: secret-detail")
    assert rig.turn() is Outcome.ERROR and "secret-detail" not in str(rig.states)
    rig.mic_result = WAV
    rig.api_result = RuntimeError("server blew up: secret-detail")
    assert rig.turn() is Outcome.ERROR and "secret-detail" not in str(rig.states + [rig.said])


def test_missing_server_audio_falls_back_to_local_speech() -> None:
    for fields in (
        {"audio": None, "audio_format": None},
        {"audio": None, "audio_format": None, "speech_error": "provider_unavailable"},
    ):
        rig = Rig()
        rig.api_result = reply(**fields)
        assert rig.turn() is Outcome.SPOKE
        assert rig.played == [] and rig.said == ["Hi there."]


def test_a_playback_failure_falls_back_to_local_speech() -> None:
    rig = Rig()
    rig.play_error = PlaybackError()
    assert rig.turn() is Outcome.SPOKE and rig.said == ["Hi there."]


def test_a_failing_local_speaker_never_breaks_the_loop() -> None:
    rig = Rig()
    rig.api_result = reply(audio=None, audio_format=None)
    rig.say_error = PlaybackError()
    assert rig.turn() is Outcome.SPOKE
    rig.api_result = ApiError("NETWORK_ERROR")
    assert rig.turn() is Outcome.ERROR


def test_run_forever_survives_exceptions_and_stops_when_asked() -> None:
    rig = Rig()
    calls = {"n": 0}

    def flaky_wait(timeout: float, *, muted: bool) -> TriggerEvent:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("trigger device failed")
        if calls["n"] >= 3:
            rig.client.stop()
        return TriggerEvent.NONE

    rig.client._trigger = type("T", (), {"wait": staticmethod(flaky_wait)})()  # type: ignore[assignment]
    rig.client._stopped.wait = lambda timeout=None: False  # type: ignore[method-assign]  # no real sleeping
    rig.client.run_forever()
    assert calls["n"] >= 3 and rig.state_names()[0] == "idle"


def test_logs_contain_states_but_never_content(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig()
    rig.api_result = reply(transcript="PRIVATESPEECH", answer="PRIVATEANSWER")
    with caplog.at_level(logging.DEBUG, logger="friday_client"):
        rig.turn()
    assert "state=listening" in caplog.text
    assert "PRIVATE" not in caplog.text


# API client


def api_with(
    handler: Callable[[httpx.Request], httpx.Response], token: str | None = "tok-secret"
) -> FridayApi:
    return FridayApi(
        "https://friday.example.com/", lambda: token, transport=httpx.MockTransport(handler)
    )


def ok_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "transcript": "hi",
        "answer": "hello",
        "conversation_id": "c1",
        "audio": {"format": "mp3", "base64": base64.b64encode(b"ID3x").decode()},
        "speech_error": None,
    }
    body.update(overrides)
    return body


def test_the_request_is_an_authenticated_multipart_upload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ok_body())

    result = api_with(handler).ask_voice(WAV, "conv-9", speak=False)
    [request] = seen
    assert str(request.url) == "https://friday.example.com/api/v1/assistant/voice"
    assert request.headers["authorization"] == "Bearer tok-secret"
    body = request.content.decode("latin-1")
    assert 'name="audio"; filename="recording.wav"' in body and "audio/wav" in body
    assert 'name="conversation_id"' in body and "conv-9" in body
    assert 'name="speak"' in body and "false" in body
    assert (result.transcript, result.answer, result.audio, result.audio_format) == (
        "hi",
        "hello",
        b"ID3x",
        "mp3",
    )


def test_a_reply_without_audio_is_valid() -> None:
    result = api_with(
        lambda r: httpx.Response(200, json=ok_body(audio=None, speech_error="provider_error"))
    ).ask_voice(WAV)
    assert result.audio is None and result.speech_error == "provider_error"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"code": "UNAUTHENTICATED", "message": "x"}), "UNAUTHENTICATED"),
        (httpx.Response(429, json={"code": "VOICE_LIMIT"}), "VOICE_LIMIT"),
        (httpx.Response(503, json={"code": "PROVIDER_UNAVAILABLE"}), "PROVIDER_UNAVAILABLE"),
        (httpx.Response(500, text="<html>oops</html>"), "BAD_RESPONSE"),
        (httpx.Response(500, json={"nope": 1}), "BAD_RESPONSE"),
        (httpx.Response(200, json={"transcript": "x"}), "BAD_RESPONSE"),
        (
            httpx.Response(200, json=ok_body(audio={"format": "mp3", "base64": "!!notbase64!!"})),
            "BAD_RESPONSE",
        ),
        (httpx.Response(302, headers={"location": "https://evil.example/"}), "BAD_RESPONSE"),
    ],
)
def test_server_responses_map_to_stable_codes(response: httpx.Response, code: str) -> None:
    with pytest.raises(ApiError) as caught:
        api_with(lambda r: response).ask_voice(WAV)
    assert caught.value.code == code and "oops" not in str(caught.value)


def test_network_failures_and_timeouts() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ApiError) as caught:
        api_with(refused).ask_voice(WAV)
    assert caught.value.code == "NETWORK_ERROR"
    with pytest.raises(ApiError) as caught:
        api_with(slow).ask_voice(WAV)
    assert caught.value.code == "TIMEOUT"


def test_without_a_token_nothing_is_sent() -> None:
    calls: list[Any] = []
    api = api_with(lambda r: calls.append(r) or httpx.Response(200, json=ok_body()), token=None)
    with pytest.raises(ApiError) as caught:
        api.ask_voice(WAV)
    assert caught.value.code == "UNAUTHENTICATED" and calls == []


def test_the_token_is_never_in_repr_or_errors() -> None:
    api = api_with(lambda r: httpx.Response(401, json={"code": "UNAUTHENTICATED"}))
    assert "tok-secret" not in repr(api)
    with pytest.raises(ApiError) as caught:
        api.ask_voice(WAV)
    assert "tok-secret" not in repr(caught.value) and "tok-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://friday.example.com",
        "ftp://x.example.com",
        "https://u:p@x.example.com",
        "https://x.example.com/?k=1",
        "nonsense",
    ],
)
def test_unsafe_server_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://friday.example.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000/",
        "http://[::1]:8000",
    ],
)
def test_https_or_local_urls_are_accepted(url: str) -> None:
    assert validate_base_url(url) == url.rstrip("/")


# audio helpers


def test_wav_encoding_round_trips() -> None:
    import io

    pcm = bytes(range(0, 200, 2)) * 10
    with wave.open(io.BytesIO(encode_wav(pcm)), "rb") as source:
        assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (
            1,
            2,
            16_000,
        )
        assert source.readframes(source.getnframes()) == pcm


def test_rms_levels() -> None:
    assert rms(b"") == 0.0 and rms(b"\x00\x00" * 100) == 0.0
    assert rms((1000).to_bytes(2, "little", signed=True) * 50) == pytest.approx(1000.0)
    assert rms((-1000).to_bytes(2, "little", signed=True) * 50) == pytest.approx(1000.0)
    assert rms(b"\x01") == 0.0  # a dangling byte is ignored


LOUD = (3000).to_bytes(2, "little", signed=True) * 480
QUIET = b"\x00\x00" * 480


def feed(detector: EndpointDetector, frames: list[bytes]) -> list[Decision]:
    return [detector.feed(frame) for frame in frames]


def test_silence_alone_ends_as_no_speech_after_the_initial_wait() -> None:
    detector = EndpointDetector(initial_wait_ms=300)
    decisions = feed(detector, [QUIET] * 12)
    assert decisions[0] is Decision.CONTINUE and Decision.NO_SPEECH in decisions


def test_speech_followed_by_silence_stops_the_recording() -> None:
    detector = EndpointDetector(silence_ms=300)
    decisions = feed(detector, [QUIET] * 3 + [LOUD] * 10 + [QUIET] * 12)
    assert Decision.NO_SPEECH not in decisions
    assert decisions.index(Decision.STOP) == 3 + 10 + 9  # 300 ms of quiet after the last loud frame


def test_pauses_shorter_than_the_silence_window_do_not_end_the_utterance() -> None:
    detector = EndpointDetector(silence_ms=300)
    decisions = feed(detector, [LOUD] * 5 + [QUIET] * 5 + [LOUD] * 5)
    assert Decision.STOP not in decisions


def test_the_maximum_duration_always_stops() -> None:
    detector = EndpointDetector(max_ms=600, silence_ms=10_000)
    decisions = feed(detector, [LOUD] * 40)
    assert decisions.index(Decision.STOP) == 19


# token stores


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, token: str) -> None:
        self.store[(service, account)] = token

    def delete_password(self, service: str, account: str) -> None:
        self.store.pop((service, account), None)


def test_keyring_store_round_trip_and_repr() -> None:
    store = KeyringTokenStore("friday.example.com", FakeKeyring())
    assert store.get() is None
    store.set("tok-secret")
    assert store.get() == "tok-secret" and "tok-secret" not in repr(store)
    store.delete()
    assert store.get() is None


def test_environment_token_wins_and_a_broken_keychain_does_not_hide_it() -> None:
    class Broken:
        def get(self) -> str:
            raise RuntimeError("keychain locked")

    env = EnvTokenStore({"FRIDAY_TOKEN": "from-env"})
    assert ChainTokenStore(env, KeyringTokenStore("a", FakeKeyring())).get() == "from-env"
    assert ChainTokenStore(Broken(), env).get() == "from-env"  # type: ignore[arg-type]
    assert ChainTokenStore(EnvTokenStore({}), KeyringTokenStore("a", FakeKeyring())).get() is None
    assert "from-env" not in repr(env)
