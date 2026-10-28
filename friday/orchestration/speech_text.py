"""Prepare an assistant answer for text-to-speech."""

import re

_FENCE = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_BULLET = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+")
_MARKUP = re.compile(r"[*_`#>|~\[\]]")
_SPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def speakable(text: str, max_chars: int) -> str:
    """Plain speech: no markdown, code, or URLs; cut at a sentence boundary within the limit."""
    text = _FENCE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _BULLET.sub("", text)
    text = _SPACE.sub(" ", _MARKUP.sub("", text)).strip()
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends and ends[-1] >= max_chars // 3:
        return window[: ends[-1]].strip()
    return window.rsplit(" ", 1)[0].strip()
