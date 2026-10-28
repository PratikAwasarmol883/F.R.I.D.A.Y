import hashlib
import re
from datetime import datetime

from friday.adapters.fetch.url_policy import canonical_url
from friday.domain.research import SourceRecord

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")


class SourceLedger:
    """Per-run record of what was actually fetched; the only valid targets of citations."""

    def __init__(self, question: str = "") -> None:
        self._sources: dict[str, SourceRecord] = {}
        self._allowed: set[str] = set()
        self.allow_urls(_URL_IN_TEXT.findall(question))

    @property
    def sources(self) -> list[SourceRecord]:
        return list(self._sources.values())

    def allow_urls(self, urls: list[str]) -> None:
        """URLs the model may fetch: search results and URLs the user wrote themselves."""
        for url in urls:
            if (canonical := canonical_url(url)) is not None:
                self._allowed.add(canonical)

    def is_allowed(self, url: str) -> bool:
        canonical = canonical_url(url)
        return canonical is not None and canonical in self._allowed

    def add(
        self,
        *,
        url: str,
        title: str,
        retrieved_at: datetime,
        text: str,
        content_type: str,
        size_bytes: int,
        excerpt_chars: int,
    ) -> SourceRecord:
        existing = next((s for s in self._sources.values() if s.url == url), None)
        if existing is not None:
            return existing
        record = SourceRecord(
            key=f"S{len(self._sources) + 1}",
            url=url,
            title=title,
            retrieved_at=retrieved_at,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            excerpt=text[:excerpt_chars],
            content_type=content_type,
            size_bytes=size_bytes,
        )
        self._sources[record.key] = record
        return record
