"""Turning model output into a trustworthy report. Pure functions, no I/O.

The model may only cite sources the server actually fetched; anything else is dropped and any
claim left without a valid citation is forced to "uncertain". URLs in the rendered report come
exclusively from stored sources, never from model text.
"""

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from friday.domain.exceptions import ReportFormatError
from friday.domain.research import ModelReport, SourceRecord
from friday.orchestration.sanitize import clean_text

CITATION_CAVEAT = (
    "Citations were checked to exist among the fetched sources, but the system cannot verify "
    "that each source actually supports the claim attributed to it."
)
NO_SOURCES_NOTE = "No sources could be retrieved; every finding is unverified."
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_\[\]<>#|])")


def parse_model_report(text: str) -> ModelReport:
    """Extract and validate the JSON object; anything else is a format error (not retried)."""
    candidate = _FENCE.sub("", text.strip())
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ReportFormatError("no JSON object in model output")
    try:
        return ModelReport.model_validate(json.loads(candidate[start : end + 1]))
    except (ValueError, ValidationError) as exc:
        raise ReportFormatError("invalid report structure") from exc


def plain_text(text: str, max_chars: int) -> str:
    """Model prose with markup, links, and markdown syntax neutralized."""
    cleaned = _URL.sub("[link removed]", clean_text(text, max_chars))
    return _MARKDOWN_SPECIALS.sub(r"\\\1", cleaned)


def _valid_keys(ids: Sequence[str], valid: set[str]) -> tuple[list[str], int]:
    kept: list[str] = []
    for source_id in ids:
        if source_id in valid and source_id not in kept:  # duplicates collapse silently
            kept.append(source_id)
    return kept, sum(1 for source_id in ids if source_id not in valid)


def build_report(model: ModelReport, sources: Sequence[SourceRecord]) -> dict[str, Any]:
    valid = {source.key for source in sources}
    dropped = 0
    findings = []
    for finding in model.findings:
        keys, bad = _valid_keys(finding.source_ids, valid)
        dropped += bad
        confidence = finding.confidence if keys else "uncertain"  # no valid support, no claim
        findings.append(
            {
                "claim": plain_text(finding.claim, 500),
                "confidence": confidence,
                "source_keys": keys,
            }
        )
    conflicts = []
    for conflict in model.conflicts:
        keys, bad = _valid_keys(conflict.source_ids, valid)
        dropped += bad
        conflicts.append(
            {"description": plain_text(conflict.description, 500), "source_keys": keys}
        )

    limitations = [plain_text(item, 300) for item in model.limitations if item.strip()]
    if dropped:
        limitations.append(f"{dropped} citation(s) referenced unknown sources and were removed.")
    if not sources:
        limitations.append(NO_SOURCES_NOTE)
    limitations.append(CITATION_CAVEAT)
    return {
        "summary": plain_text(model.summary, 2000),
        "findings": findings,
        "conflicts": conflicts,
        "limitations": limitations,
    }


def render_markdown(
    question: str,
    content: dict[str, Any],
    sources: Sequence[Any],
) -> str:
    """`sources` need `.source_key`, `.title`, `.url`, `.retrieved_at` (stored rows or records)."""

    def cite(keys: Sequence[str]) -> str:
        return "".join(f"[{key}]" for key in keys)

    lines = ["# Research report", "", f"**Question:** {plain_text(question, 1000)}", ""]
    lines += ["## Summary", "", content["summary"], "", "## Findings", ""]
    for number, finding in enumerate(content["findings"], start=1):
        flag = "" if finding["confidence"] == "supported" else "*(uncertain)*"
        parts = [finding["claim"], cite(finding["source_keys"]), flag]
        lines.append(f"{number}. " + " ".join(part for part in parts if part))
    if not content["findings"]:
        lines.append("_No findings._")
    if content["conflicts"]:
        lines += ["", "## Conflicting information", ""]
        for conflict in content["conflicts"]:
            parts = [conflict["description"], cite(conflict["source_keys"])]
            lines.append("- " + " ".join(part for part in parts if part))
    lines += ["", "## Limitations", ""]
    lines += [f"- {item}" for item in content["limitations"]]
    lines += ["", "## Sources", ""]
    for source in sources:
        title = plain_text(source.title or source.url, 200)
        retrieved = source.retrieved_at.isoformat()
        lines.append(f"[{source.source_key}] {title} - {source.url} (retrieved {retrieved})")
    if not sources:
        lines.append("_No sources were retrieved._")
    return "\n".join(lines) + "\n"
