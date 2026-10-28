"""Audio upload validation by content, never by the declared type."""

MIN_AUDIO_BYTES = 12


def detect_audio_format(data: bytes) -> str | None:
    """Return a provider-friendly extension for supported audio, or None."""
    if len(data) < MIN_AUDIO_BYTES:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a" if data[8:12] == b"M4A " else "mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    return None
