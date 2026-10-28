"""Audio helpers and macOS adapters. The pure parts are tested; the hardware parts are not."""

import array
import io
import math
import os
import subprocess
import sys
import tempfile
import threading
import wave
from enum import StrEnum

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


class AudioDeviceError(Exception):
    """No usable microphone or recording failure."""


class PlaybackError(Exception):
    """Audio could not be played."""


def encode_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Wrap 16-bit mono PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def rms(pcm: bytes) -> float:
    """Root-mean-square level of 16-bit little-endian samples (pure Python)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Decision(StrEnum):
    CONTINUE = "continue"
    STOP = "stop"
    NO_SPEECH = "no_speech"


class EndpointDetector:
    """Decides when an utterance has ended: speech, then a stretch of silence, or a time cap."""

    def __init__(
        self,
        *,
        threshold: float = 500.0,
        silence_ms: int = 1000,
        max_ms: int = 15_000,
        initial_wait_ms: int = 4_000,
    ) -> None:
        self._threshold = threshold
        self._silence_ms = silence_ms
        self._max_ms = max_ms
        self._initial_wait_ms = initial_wait_ms
        self._elapsed_ms = 0
        self._quiet_ms = 0
        self._heard_speech = False

    def feed(self, frame: bytes, frame_ms: int = FRAME_MS) -> Decision:
        self._elapsed_ms += frame_ms
        if rms(frame) >= self._threshold:
            self._heard_speech = True
            self._quiet_ms = 0
        else:
            self._quiet_ms += frame_ms
        if not self._heard_speech:
            return (
                Decision.NO_SPEECH
                if self._elapsed_ms >= self._initial_wait_ms
                else Decision.CONTINUE
            )
        if self._quiet_ms >= self._silence_ms or self._elapsed_ms >= self._max_ms:
            return Decision.STOP
        return Decision.CONTINUE


class SoundDeviceMicrophone:
    """Records one utterance from the default input device (requires `sounddevice`)."""

    def __init__(self, detector_factory: type[EndpointDetector] = EndpointDetector) -> None:
        self._detector_factory = detector_factory

    def record_utterance(self) -> bytes:
        try:
            import sounddevice  # lazy: needs PortAudio and a real device
        except ImportError as exc:
            raise AudioDeviceError("sounddevice is not installed") from exc
        detector = self._detector_factory()
        chunks: list[bytes] = []
        try:
            with sounddevice.RawInputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES
            ) as stream:
                while True:
                    frame, _overflowed = stream.read(FRAME_SAMPLES)
                    data = bytes(frame)
                    chunks.append(data)
                    decision = detector.feed(data)
                    if decision is Decision.STOP:
                        break
                    if decision is Decision.NO_SPEECH:
                        raise NoSpeechCaptured
        except NoSpeechCaptured:
            raise
        except Exception as exc:  # PortAudioError and friends: no device, permission denied, ...
            raise AudioDeviceError(type(exc).__name__) from exc
        return encode_wav(b"".join(chunks))


class NoSpeechCaptured(Exception):
    """The microphone opened but nobody spoke."""


class AfplayPlayer:
    """Plays audio with macOS `afplay`; `stop()` interrupts immediately."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def play(self, audio: bytes, audio_format: str) -> None:
        suffix = ".mp3" if audio_format == "mp3" else f".{audio_format}"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="friday-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(audio)
            with self._lock:
                self._process = subprocess.Popen(["afplay", path])
            self._process.wait()
        except OSError as exc:
            raise PlaybackError from exc
        finally:
            with self._lock:
                self._process = None
            try:
                os.unlink(path)
            except OSError:
                pass

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()


class SayLocalSpeaker:
    """Local fallback speech via macOS `say`; text goes through stdin, never the command line."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def say(self, text: str) -> None:
        try:
            with self._lock:
                self._process = subprocess.Popen(["say"], stdin=subprocess.PIPE)
            self._process.communicate(text.encode())
        except OSError as exc:
            raise PlaybackError from exc
        finally:
            with self._lock:
                self._process = None

    def stop(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
