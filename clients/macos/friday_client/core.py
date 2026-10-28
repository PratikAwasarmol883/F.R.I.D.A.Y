"""The voice client state machine. Hardware-independent: every device sits behind a protocol."""

import logging
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from friday_client.api import ApiError, VoiceReply
from friday_client.audio import AudioDeviceError, NoSpeechCaptured, PlaybackError

logger = logging.getLogger("friday_client")

CONVERSATION_IDLE_SECONDS = 30 * 60


class State(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"
    ERROR = "error"


class Outcome(StrEnum):
    IDLE_TICK = "idle_tick"
    MUTED = "muted"
    SPOKE = "spoke"
    NO_SPEECH = "no_speech"
    ERROR = "error"
    QUIT = "quit"


class TriggerEvent(StrEnum):
    NONE = "none"
    TALK = "talk"
    TOGGLE_MUTE = "toggle_mute"
    QUIT = "quit"


class Microphone(Protocol):
    def record_utterance(self) -> bytes: ...


class Trigger(Protocol):
    def wait(self, timeout: float, *, muted: bool) -> TriggerEvent:
        """Block up to `timeout`. While `muted` an implementation must not capture any audio."""
        ...


class Player(Protocol):
    def play(self, audio: bytes, audio_format: str) -> None: ...
    def stop(self) -> None: ...


class LocalSpeaker(Protocol):
    def say(self, text: str) -> None: ...
    def stop(self) -> None: ...


class VoiceApi(Protocol):
    def ask_voice(
        self, wav: bytes, conversation_id: str | None = None, *, speak: bool = True
    ) -> VoiceReply: ...


Indicator = Callable[[State, str], None]

# What the user sees and hears for each failure; never any server-provided text.
ERROR_MESSAGES = {
    "NO_MICROPHONE": "I can't use the microphone.",
    "UNAUTHENTICATED": "I'm not signed in. Run the login command.",
    "VOICE_LIMIT": "The daily voice limit has been reached.",
    "BUDGET_EXCEEDED": "The daily usage budget has been reached.",
    "NO_SPEECH_DETECTED": "I didn't catch that.",
    "PAYLOAD_TOO_LARGE": "That recording was too long.",
    "UNSUPPORTED_MEDIA_TYPE": "The recording could not be read.",
    "PROVIDER_TIMEOUT": "The assistant took too long to respond.",
    "PROVIDER_UNAVAILABLE": "The assistant is unavailable right now.",
    "PROVIDER_ERROR": "The assistant could not answer.",
    "NETWORK_ERROR": "I can't reach the server.",
    "TIMEOUT": "The server took too long to respond.",
}
DEFAULT_ERROR = "Something went wrong."


class VoiceClient:
    def __init__(
        self,
        *,
        mic: Microphone,
        trigger: Trigger,
        player: Player,
        local_speaker: LocalSpeaker,
        api: VoiceApi,
        indicator: Indicator,
        clock: Callable[[], float] = time.monotonic,
        conversation_idle_seconds: float = CONVERSATION_IDLE_SECONDS,
    ) -> None:
        self._mic = mic
        self._trigger = trigger
        self._player = player
        self._local = local_speaker
        self._api = api
        self._indicator = indicator
        self._clock = clock
        self._conversation_idle = conversation_idle_seconds
        self._muted = threading.Event()
        self._stopped = threading.Event()
        self._state: State | None = None
        self._conversation_id: str | None = None
        self._last_turn = 0.0

    # ----- controls (safe to call from another thread) -----

    @property
    def muted(self) -> bool:
        return self._muted.is_set()

    @property
    def state(self) -> State | None:
        return self._state

    def mute(self) -> None:
        """Takes effect immediately: playback stops and nothing is captured or sent."""
        self._muted.set()
        self._player.stop()
        self._local.stop()
        self._set(State.MUTED, "Muted")

    def unmute(self) -> None:
        self._muted.clear()
        self._set(State.IDLE)

    def stop(self) -> None:
        self._stopped.set()
        self._player.stop()
        self._local.stop()

    def _set(self, state: State, detail: str = "") -> None:
        if state is self._state and not detail:
            return
        self._state = state
        logger.info("state=%s", state.value)
        self._indicator(state, detail)

    # ----- one turn -----

    def run_once(self, poll_seconds: float = 0.25) -> Outcome:
        event = self._trigger.wait(poll_seconds, muted=self.muted)
        if event is TriggerEvent.QUIT:
            self.stop()
            return Outcome.QUIT
        if event is TriggerEvent.TOGGLE_MUTE:
            self.unmute() if self.muted else self.mute()
        if self.muted:
            self._set(State.MUTED, "Muted")
            return Outcome.MUTED
        if event is not TriggerEvent.TALK:
            self._set(State.IDLE)
            return Outcome.IDLE_TICK
        return self._turn()

    def _turn(self) -> Outcome:
        self._set(State.LISTENING, "Listening")  # visible before any audio is captured
        try:
            wav = self._mic.record_utterance()
        except NoSpeechCaptured:
            self._set(State.IDLE, "I didn't hear anything")
            return Outcome.NO_SPEECH
        except AudioDeviceError:
            return self._fail("NO_MICROPHONE")
        except Exception:
            logger.exception("recording failed")
            return self._fail("UNEXPECTED")
        if self.muted:  # muted while recording: the audio is discarded, never sent
            return Outcome.MUTED

        self._set(State.THINKING, "Thinking")
        try:
            reply = self._api.ask_voice(wav, self._current_conversation())
        except ApiError as exc:
            logger.warning("api error code=%s", exc.code)
            return self._fail(exc.code)
        except Exception:
            logger.exception("request failed")
            return self._fail("UNEXPECTED")
        if self.muted:
            return Outcome.MUTED

        self._conversation_id, self._last_turn = reply.conversation_id, self._clock()
        self._set(State.SPEAKING, "Speaking")
        self._speak(reply)
        if not self.muted:
            self._set(State.IDLE)
        return Outcome.SPOKE

    def _current_conversation(self) -> str | None:
        if self._conversation_id and self._clock() - self._last_turn < self._conversation_idle:
            return self._conversation_id
        self._conversation_id = None
        return None

    def _speak(self, reply: VoiceReply) -> None:
        if reply.audio and reply.audio_format:
            try:
                self._player.play(reply.audio, reply.audio_format)
                return
            except PlaybackError:
                logger.warning("playback failed; using local speech")
        if reply.answer and not self.muted:
            self._say(reply.answer)

    def _say(self, text: str) -> None:
        try:
            self._local.say(text)
        except Exception:
            logger.warning("local speech failed")

    def _fail(self, code: str) -> Outcome:
        message = ERROR_MESSAGES.get(code, DEFAULT_ERROR)
        self._set(State.ERROR, message)
        if not self.muted:
            self._say(message)
        self._set(State.IDLE)
        return Outcome.ERROR

    # ----- main loop -----

    def run_forever(self) -> None:
        self._set(State.IDLE, "Ready")
        while not self._stopped.is_set():
            try:
                if self.run_once() is Outcome.MUTED:
                    self._stopped.wait(0.2)
            except Exception:  # the loop must survive anything a device or server throws at it
                logger.exception("unexpected error in the voice loop")
                self._stopped.wait(1.0)
