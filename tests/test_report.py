import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from friday.domain.exceptions import ReportFormatError
from friday.domain.research import ModelReport, SourceRecord
from friday.orchestration.report import (
    CITATION_CAVEAT,
    NO_SOURCES_NOTE,
    build_report,
    parse_model_report,
    plain_text,
    render_markdown,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def source(key: str, url: str = "", title: str = "") -> SourceRecord:
    return SourceRecord(
        key=key,
        url=url or f"https://example.com/{key.lower()}",
        title=title or f"Title {key}",
        retrieved_at=NOW,
        content_hash="a" * 64,
        excerpt="x",
        content_type="text/html",
        size_bytes=10,
    )


GOOD = {
    "summary": "X is Y.",
    "findings": [{"claim": "X is Y", "source_ids": ["S1"], "confidence": "supported"}],
    "conflicts": [],
    "limitations": ["Small sample."],
}


# ----- parsing -----


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(GOOD),
        "```json\n" + json.dumps(GOOD) + "\n```",
        "```\n" + json.dumps(GOOD) + "\n```",
        "Here is the report:\n" + json.dumps(GOOD) + "\nHope that helps!",
        "  \n" + json.dumps({**GOOD, "unexpected": [1, 2]}) + "  ",
    ],
)
def test_valid_reports_parse_in_common_wrappers(text: str) -> None:
    report = parse_model_report(text)
    assert report.summary == "X is Y." and report.findings[0].source_ids == ["S1"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no json here",
        "{not json}",
        "[]",
        json.dumps({"findings": []}),  # summary missing
        json.dumps({"summary": ""}),
        json.dumps({"summary": "x" * 2001}),
        json.dumps({"summary": "ok", "findings": [{"claim": "", "source_ids": []}]}),
        json.dumps({"summary": "ok", "findings": [{"claim": "c", "confidence": "certain"}]}),
        json.dumps({"summary": "ok", "findings": [{"claim": "c"}] * 21}),
        json.dumps({"summary": "ok", "findings": "not a list"}),
    ],
)
def test_malformed_reports_are_format_errors(text: str) -> None:
    with pytest.raises(ReportFormatError):
        parse_model_report(text)


def test_format_errors_are_provider_errors_so_tasks_do_not_retry_them() -> None:
    from friday.domain.exceptions import ProviderError
    from friday.domain.task_state import RETRYABLE_CODES, error_code_for

    error = ReportFormatError("bad")
    assert isinstance(error, ProviderError)
    assert error_code_for(error) not in RETRYABLE_CODES


# ----- validation: citations must be real -----


def build(model: dict[str, object], sources: list[SourceRecord]) -> dict[str, object]:
    return build_report(ModelReport.model_validate(model), sources)


def test_unknown_citations_are_dropped_and_uncited_claims_become_uncertain() -> None:
    model = {
        "summary": "s",
        "findings": [
            {"claim": "valid", "source_ids": ["S1", "S1", "S2"], "confidence": "supported"},
            {
                "claim": "invented",
                "source_ids": ["S9", "http://evil.example"],
                "confidence": "supported",
            },
            {"claim": "none", "source_ids": [], "confidence": "supported"},
            {"claim": "hedged", "source_ids": ["S2"], "confidence": "uncertain"},
        ],
        "conflicts": [{"description": "they differ", "source_ids": ["S1", "S7"]}],
    }
    report = build(model, [source("S1"), source("S2")])
    findings = report["findings"]  # type: ignore[index]
    assert [(f["confidence"], f["source_keys"]) for f in findings] == [
        ("supported", ["S1", "S2"]),
        ("uncertain", []),
        ("uncertain", []),
        ("uncertain", ["S2"]),
    ]
    assert report["conflicts"] == [{"description": "they differ", "source_keys": ["S1"]}]  # type: ignore[index]
    limitations = report["limitations"]  # type: ignore[index]
    assert "3 citation(s) referenced unknown sources and were removed." in limitations
    assert limitations[-1] == CITATION_CAVEAT


def test_a_report_without_sources_is_entirely_uncertain_and_says_so() -> None:
    report = build(GOOD, [])
    assert [f["confidence"] for f in report["findings"]] == ["uncertain"]  # type: ignore[index]
    assert NO_SOURCES_NOTE in report["limitations"]  # type: ignore[operator]


def test_the_citation_caveat_is_always_present() -> None:
    assert CITATION_CAVEAT in build({"summary": "s"}, [source("S1")])["limitations"]  # type: ignore[operator]


# ----- model text can never smuggle links or markup -----


@pytest.mark.parametrize(
    "text",
    [
        "see [click here](http://evil.example/steal?d=1)",
        "![img](https://evil.example/pixel.png)",
        "visit https://evil.example/x now",
        "go to www.evil.example",
        "<script>alert(1)</script> and <img src=x onerror=alert(1)>",
        "line\n# Heading injection\n[S1]: http://evil.example",
    ],
)
def test_model_prose_is_stripped_of_links_and_markup(text: str) -> None:
    cleaned = plain_text(text, 500)
    assert "evil.example" not in cleaned and "<" not in cleaned and ">" not in cleaned
    assert "](" not in cleaned.replace("\\]", "").replace("\\[", "")


def test_rendered_markdown_links_only_stored_sources() -> None:
    sources = [source("S1", "https://real.example/a", "Real *bold* [title](http://evil.example)")]
    model = {
        "summary": "Check https://evil.example/summary please",
        "findings": [
            {
                "claim": "Claim [x](http://evil.example)",
                "source_ids": ["S1"],
                "confidence": "supported",
            }
        ],
    }
    content = build(model, sources)
    rows = [
        SimpleNamespace(source_key=s.key, title=s.title, url=s.url, retrieved_at=s.retrieved_at)
        for s in sources
    ]
    markdown = render_markdown("Question? https://evil.example/q", content, rows)
    assert markdown.count("https://real.example/a") == 1
    assert "evil.example" not in markdown
    assert "1. " in markdown and "[S1]" in markdown
    assert f"retrieved {NOW.isoformat()}" in markdown


def test_uncertain_findings_are_flagged_in_the_markdown() -> None:
    content = build(
        {"summary": "s", "findings": [{"claim": "maybe", "source_ids": []}]}, [source("S1")]
    )
    markdown = render_markdown("q", content, [])
    assert "maybe *(uncertain)*" in markdown
    assert "_No sources were retrieved._" in markdown
