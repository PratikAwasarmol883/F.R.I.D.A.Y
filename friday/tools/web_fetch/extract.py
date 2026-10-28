"""Plain-text extraction from fetched pages using only the standard library."""

from dataclasses import dataclass
from html.parser import HTMLParser

from friday.orchestration.sanitize import clean_text

_SKIP = frozenset(
    {"script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "aside",
     "form", "button", "select", "canvas", "object", "embed"}
)  # fmt: skip
_BLOCK = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "main", "header",
     "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"}
)  # fmt: skip
_MAX_SKIP_DEPTH = 200  # unclosed script/style must not swallow the whole page forever


@dataclass(frozen=True)
class ExtractedText:
    title: str
    text: str


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP:
            self._skip_depth = min(self._skip_depth + 1, _MAX_SKIP_DEPTH)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif self._skip_depth == 0:
            self.parts.append(data)


def extract_text(
    raw: str, content_type: str, max_chars: int, title_chars: int = 200
) -> ExtractedText:
    """Title and readable text, cleaned of markup, control and invisible characters."""
    if content_type == "text/plain":
        return ExtractedText("", clean_text(raw, max_chars))
    parser = _Extractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # malformed markup must never break a research run
        return ExtractedText("", clean_text(raw, max_chars))
    return ExtractedText(
        title=clean_text(" ".join(parser.title_parts), title_chars),
        text=clean_text(" ".join(parser.parts), max_chars),
    )
