import io
import wave

import pytest
from pydantic import ValidationError

from friday.core.config import Settings
from friday.orchestration.audio import detect_audio_format
from friday.orchestration.speech_text import speakable


def wav_bytes(seconds: float = 0.1, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


PAD = b"\x00" * 16


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (wav_bytes(), "wav"),
        (b"ID3\x04\x00\x00" + PAD, "mp3"),
        (b"\xff\xfb\x90\x00" + PAD, "mp3"),
        (b"\xff\xf3\x40\xc0" + PAD, "mp3"),
        (b"\x00\x00\x00\x20ftypM4A \x00\x00" + PAD, "m4a"),
        (b"\x00\x00\x00\x20ftypisom\x00\x00" + PAD, "mp4"),
        (b"\x1a\x45\xdf\xa3" + PAD, "webm"),
        (b"OggS\x00\x02" + PAD, "ogg"),
        (b"fLaC\x00\x00" + PAD, "flac"),
    ],
)
def test_supported_formats_are_recognized_by_content(data: bytes, expected: str) -> None:
    assert detect_audio_format(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"RIFF",
        b"RIFF\x00\x00\x00\x00AVI " + PAD,  # a RIFF container that is not WAVE
        b"MZ" + PAD * 2,  # a Windows executable
        b"\x7fELF" + PAD * 2,
        b"#!/bin/sh\n" + PAD,
        b"<html><body>hi</body></html>" + PAD,
        b"%PDF-1.7" + PAD,
        b"\xff\x00" + PAD,  # frame sync byte without sync bits
        b"PK\x03\x04" + PAD,
        bytes(range(256)),
    ],
)
def test_everything_else_is_rejected(data: bytes) -> None:
    assert detect_audio_format(data) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hello **world**!", "Hello world!"),
        ("See [the docs](https://docs.example/x) for more.", "See the docs for more."),
        ("Visit https://example.com/a?b=1 now", "Visit a link now"),
        ("Go to www.example.com today", "Go to a link today"),
        ("Use `pip install x` first.", "Use pip install x first."),
        ("Before\n```python\nprint('hi')\n```\nAfter", "Before After"),
        ("# Title\n- one\n- two\n1. three", "Title one two three"),
        ("a   lot\n\n of   space", "a lot of space"),
        ("", ""),
    ],
)
def test_markdown_urls_and_code_are_not_read_aloud(raw: str, expected: str) -> None:
    assert speakable(raw, 1500) == expected


def test_long_answers_are_cut_at_a_sentence_boundary() -> None:
    text = "First sentence is here. " * 100
    spoken = speakable(text, 200)
    assert len(spoken) <= 200 and spoken.endswith(".") and spoken.startswith("First sentence")


def test_unbroken_text_is_cut_at_a_word_boundary() -> None:
    spoken = speakable("word " * 200, 50)
    assert len(spoken) <= 50 and not spoken.endswith(" ") and spoken.split()[-1] == "word"


def test_speech_base_url_rules() -> None:
    assert Settings(_env_file=None).speech_base_url == "https://api.openai.com/v1"
    assert Settings(
        _env_file=None, speech_base_url="https://api.groq.com/openai/v1/"
    ).speech_base_url == ("https://api.groq.com/openai/v1")
    for local in ("http://localhost:8080/v1", "http://127.0.0.1:9000", "http://[::1]:9000/v1"):
        assert Settings(_env_file=None, speech_base_url=local)
    for bad in (
        "http://api.example.com/v1",
        "ftp://example.com",
        "https://user:pw@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://example.com/v1#frag",
        "https:///nohost",
        "not a url",
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, speech_base_url=bad)


def test_speech_configuration_flag_requires_a_nonempty_key() -> None:
    assert Settings(_env_file=None).speech_configured is False
    assert Settings(_env_file=None, speech_api_key="").speech_configured is False  # type: ignore[arg-type]
    assert Settings(_env_file=None, speech_api_key="k").speech_configured is True  # type: ignore[arg-type]
