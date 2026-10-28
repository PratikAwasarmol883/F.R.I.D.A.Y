import asyncio
import os

import pytest

from friday.adapters.speech.openai_compatible import OpenAICompatibleSpeech
from friday.core.config import Settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("SPEECH_API_KEY"),
    reason="SPEECH_API_KEY not set (opt-in live test against a real speech provider)",
)


def test_synthesis_then_transcription_round_trip() -> None:
    config = Settings(_env_file=None)
    speech = OpenAICompatibleSpeech(
        api_key=os.environ["SPEECH_API_KEY"],
        base_url=config.speech_base_url,
        stt_model=config.stt_model,
        tts_model=config.tts_model,
        voice=config.tts_voice,
    )

    async def scenario() -> str:
        audio = await speech.synthesize("Hello from FRIDAY.", 30)
        assert audio.audio_format == "mp3" and len(audio.data) > 1_000
        heard = await speech.transcribe(audio.data, "mp3", 30)
        await speech.aclose()
        return heard.text

    assert "hello" in asyncio.run(scenario()).lower()
