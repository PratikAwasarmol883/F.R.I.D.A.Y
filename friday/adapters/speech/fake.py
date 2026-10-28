from friday.adapters.speech.base import SynthesizedAudio, Transcript


class FakeSpeech:
    """Stands in for both providers in tests; records calls and can be told to fail."""

    def __init__(
        self,
        transcript: str = "what is two plus two",
        audio: bytes = b"ID3-fake-spoken-audio",
        stt_error: Exception | None = None,
        tts_error: Exception | None = None,
    ) -> None:
        self.transcript = transcript
        self.audio = audio
        self.stt_error = stt_error
        self.tts_error = tts_error
        self.transcriptions: list[tuple[int, str]] = []  # (byte length, format)
        self.syntheses: list[str] = []

    async def transcribe(
        self, audio: bytes, audio_format: str, timeout_seconds: float
    ) -> Transcript:
        self.transcriptions.append((len(audio), audio_format))
        if self.stt_error is not None:
            raise self.stt_error
        return Transcript(text=self.transcript)

    async def synthesize(self, text: str, timeout_seconds: float) -> SynthesizedAudio:
        self.syntheses.append(text)
        if self.tts_error is not None:
            raise self.tts_error
        return SynthesizedAudio(data=self.audio, audio_format="mp3")
