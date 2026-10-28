from typing import Any
from urllib.parse import urlsplit

from friday.adapters.fetch.base import FetchError, PageFetcher
from friday.adapters.fetch.url_policy import validate_addresses, validate_url
from friday.core.redaction import contains_known_secret
from friday.domain.enums import RiskClass
from friday.orchestration.tool_contracts import Source, Tool, ToolContract, ToolError, ToolSummary
from friday.tools.web_fetch.extract import extract_text
from friday.tools.web_fetch.ledger import SourceLedger
from friday.tools.web_fetch.schemas import WebFetchInput, WebFetchOutput

PAGE_TEXT_CHARS = 20_000
MODEL_TEXT_CHARS = 6_000
EXCERPT_CHARS = 1_000


class WebFetchTool(Tool[WebFetchInput, WebFetchOutput]):
    def __init__(
        self,
        fetcher: PageFetcher,
        ledger: SourceLedger,
        *,
        timeout_seconds: float,
        max_calls_per_ask: int,
        daily_limit: int,
        model_text_chars: int = MODEL_TEXT_CHARS,
    ) -> None:
        self._fetcher = fetcher
        self._ledger = ledger
        self._model_text_chars = model_text_chars
        self.contract = ToolContract(
            name="web_fetch",
            version="1",
            description=(
                "Fetch a public web page and return its readable text. Only URLs that appeared "
                "in search results or in the user's question may be fetched."
            ),
            input_model=WebFetchInput,
            output_model=WebFetchOutput,
            risk_class=RiskClass.READ_ONLY,
            required_scope="tool:web_fetch",
            timeout_seconds=timeout_seconds,
            max_retries=0,
            returns_untrusted_content=True,
            max_calls_per_ask=max_calls_per_ask,
            daily_limit=daily_limit,
        )

    def guard(self, arguments: WebFetchInput) -> str | None:
        url = arguments.url
        try:
            parsed = validate_url(url)
            if parsed.is_ip_literal:
                validate_addresses([parsed.host])  # e.g. a hostile result listing 127.0.0.1
        except FetchError as exc:
            return exc.code
        if contains_known_secret(url):
            return "secret_in_url"
        if not self._ledger.is_allowed(url):
            return "url_not_allowed"  # blocks model-built URLs that could carry data out
        return None

    def redact_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments.get("url")
        if isinstance(url, str):
            parts = urlsplit(url)
            masked = parts._replace(query="…" if parts.query else "", fragment="").geturl()
            return {**arguments, "url": masked[:300]}
        return arguments

    async def run(self, arguments: WebFetchInput) -> WebFetchOutput:
        try:
            page = await self._fetcher.fetch(arguments.url, self.contract.timeout_seconds)
        except FetchError as exc:
            raise ToolError(exc.code) from exc
        extracted = extract_text(page.text, page.content_type, PAGE_TEXT_CHARS)
        record = self._ledger.add(
            url=page.url,
            title=extracted.title,
            retrieved_at=page.retrieved_at,
            text=extracted.text,
            content_type=page.content_type,
            size_bytes=page.size_bytes,
            excerpt_chars=EXCERPT_CHARS,
        )
        return WebFetchOutput(
            source_id=record.key,
            url=record.url,
            title=record.title,
            retrieved_at=record.retrieved_at.isoformat(),
            text=extracted.text[: self._model_text_chars],
        )

    def summarize(self, output: WebFetchOutput) -> ToolSummary:
        host = urlsplit(output.url).hostname or ""
        return ToolSummary(
            meta={"source_id": output.source_id, "host": host, "chars": len(output.text)},
            sources=(Source(output.title or output.url, output.url),),
        )
