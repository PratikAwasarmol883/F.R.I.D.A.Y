from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Transcript:
    text: str


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    audio_format: str


class TranscriptionProvider(Protocol):
    async def transcribe(
        self, audio: bytes, audio_format: str, timeout_seconds: float
    ) -> Transcript: ...


class SpeechProvider(Protocol):
    async def synthesize(self, text: str, timeout_seconds: float) -> SynthesizedAudio: ...
