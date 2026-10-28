import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from friday.persistence.models import Report, ResearchSource, Task

from .conftest import Seeded, SessionFactory
from .helpers import audit_rows, count
from .research_helpers import A_URL, Env, produce_report
from .task_helpers import fetch, sql
from .worker_handlers import registry_for
from .worker_helpers import TERMINAL, drive, settings_for, state, wait_until

KEY = {"search_api_key": SecretStr("tvly-abcdefgh12345678")}
RESEARCH = "/api/v1/research"


def post(
    client: TestClient,
    who: Seeded,
    question: str = "What is X?",
    key: str | None = None,
    **extra: Any,
) -> Any:
    headers = dict(who.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(RESEARCH, json={"question": question, **extra}, headers=headers)


# submission


def test_research_needs_authentication_and_both_tool_scopes(
    make_client: Any, alice: Seeded, alice_tools: Seeded, alice_research: Seeded
) -> None:
    client = make_client(None, **KEY)
    assert client.post(RESEARCH, json={"question": "x"}).status_code == 401
    for who in (alice, alice_tools):  # none, or only tool:web_search
        denied = post(client, who)
        assert denied.status_code == 403 and denied.json()["code"] == "FORBIDDEN"
    assert post(client, alice_research).status_code == 202


def test_research_is_unavailable_without_a_search_key_or_with_tools_disabled(
    make_client: Any, alice_research: Seeded, session_factory: SessionFactory
) -> None:
    for overrides in ({}, {**KEY, "enabled_tools": "web_search"}, {**KEY, "enabled_tools": ""}):
        response = post(make_client(None, **overrides), alice_research)
        assert response.status_code == 503 and response.json()["code"] == "PROVIDER_UNAVAILABLE"
    assert count(session_factory, Task) == 0


def test_a_submission_creates_a_research_task_with_the_stored_input(
    make_client: Any, alice_research: Seeded, session_factory: SessionFactory
) -> None:
    response = post(make_client(None, **KEY), alice_research, "  What is X?  ", max_sources=3)
    assert response.status_code == 202
    body = response.json()
    assert (body["type"], body["status"], body["result"]) == ("research.run", "queued", None)
    assert response.headers["Location"] == f"/api/v1/tasks/{body['id']}"
    stored = fetch(session_factory, uuid.UUID(body["id"]))
    assert stored.input == {"question": "What is X?", "max_sources": 3}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 1001},
        {"question": "ok", "max_sources": 0},
        {"question": "ok", "max_sources": 11},
        {"question": "ok", "max_sources": "3"},
        {"question": "ok", "extra": 1},
    ],
)
def test_invalid_research_requests_are_rejected(
    make_client: Any,
    alice_research: Seeded,
    session_factory: SessionFactory,
    payload: dict[str, Any],
) -> None:
    client = make_client(None, **KEY)
    response = client.post(RESEARCH, json=payload, headers=alice_research.headers)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert count(session_factory, Task) == 0


def test_idempotency_and_limits_apply_to_research_tasks(
    make_client: Any, alice_research: Seeded
) -> None:
    client = make_client(None, max_active_tasks_per_user=1, **KEY)
    first = post(client, alice_research, "same", key="r-1")
    assert (first.status_code, post(client, alice_research, "same", key="r-1").status_code) == (
        202,
        200,
    )
    conflict = post(client, alice_research, "different", key="r-1")
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    limited = post(client, alice_research, "another")
    assert limited.status_code == 429 and limited.json()["code"] == "TASK_LIMIT"


# reports


def test_report_shape_markdown_and_source_metadata(
    client: TestClient, alice_research: Seeded, session_factory: SessionFactory
) -> None:
    task_id, report_id = produce_report(session_factory, alice_research)
    body = client.get(f"/api/v1/reports/{report_id}", headers=alice_research.headers).json()
    assert (
        body["question"] == "What is the answer?" and body["summary"] == "The answer is forty-two."
    )
    [finding] = body["findings"]
    assert (finding["claim"], finding["confidence"]) == ("It is forty-two", "supported")
    assert [(s["key"], s["url"], s["title"]) for s in finding["sources"]] == [
        ("S1", A_URL, "Alpha Page")
    ]
    assert finding["sources"][0]["retrieved_at"]
    [source] = body["sources"]
    assert len(source["content_hash"]) == 64 and source["url"] == A_URL
    assert body["markdown"].count(A_URL) == 1 and "[S1]" in body["markdown"]
    assert any("cannot verify" in item for item in body["limitations"])
    assert body["id"] == report_id and task_id


def test_a_succeeded_research_task_points_at_its_report(
    client: TestClient, alice_research: Seeded, session_factory: SessionFactory
) -> None:
    task_id, report_id = produce_report(session_factory, alice_research)
    body = client.get(f"/api/v1/tasks/{task_id}", headers=alice_research.headers).json()
    assert body["status"] == "succeeded" and body["type"] == "research.run"
    assert body["result"] == {
        "report_id": report_id,
        "usage": {"input_tokens": 30, "output_tokens": 15},
        "sources_count": 1,
    }


def test_reports_are_owner_scoped(
    client: TestClient, alice_research: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    _, report_id = produce_report(session_factory, alice_research)
    url = f"/api/v1/reports/{report_id}"
    foreign = client.get(url, headers=bob.headers)
    missing = client.get(f"/api/v1/reports/{uuid.uuid4()}", headers=bob.headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["message"] == missing.json()["message"]
    assert client.delete(url, headers=bob.headers).status_code == 404
    assert (
        client.get(url).status_code == 401
        and client.get("/api/v1/reports/nope", headers=bob.headers).status_code == 422
    )
    assert client.get(url, headers=alice_research.headers).status_code == 200


def test_deleting_a_report_removes_it_and_its_sources_and_is_audited(
    client: TestClient, alice_research: Seeded, session_factory: SessionFactory
) -> None:
    _, report_id = produce_report(session_factory, alice_research)
    url = f"/api/v1/reports/{report_id}"
    assert client.delete(url, headers=alice_research.headers).status_code == 204
    assert client.get(url, headers=alice_research.headers).status_code == 404
    assert client.delete(url, headers=alice_research.headers).status_code == 404
    assert count(session_factory, Report) == 0 and count(session_factory, ResearchSource) == 0
    [audit] = audit_rows(session_factory, "report.delete")
    assert audit.target_id == report_id and audit.outcome == "success"


def test_a_report_outlives_its_task(
    client: TestClient, alice_research: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task_id, report_id = produce_report(session_factory, alice_research)
    sql(test_db_url, "DELETE FROM tasks WHERE id = %s", (task_id,))  # e.g. retention purge
    assert (
        client.get(f"/api/v1/reports/{report_id}", headers=alice_research.headers).status_code
        == 200
    )


# end to end


def test_submit_disconnect_worker_researches_and_the_report_is_fetchable_later(
    make_client: Any, alice_research: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    submit = make_client(None, **KEY)
    task_id = post(submit, alice_research, "What is the answer?").json()["id"]
    submit.close()  # the caller disconnects

    env = Env()
    handlers = registry_for(env.handler(session_factory, settings_for()))

    async def scenario(_: list[Any]) -> None:
        await wait_until(lambda: state(test_db_url, task_id)[0] in TERMINAL)

    drive(session_factory, handlers, scenario)
    assert state(test_db_url, task_id) == ("succeeded", 1, None)

    later = make_client(None)
    task = later.get(f"/api/v1/tasks/{task_id}", headers=alice_research.headers).json()
    report = later.get(
        f"/api/v1/reports/{task['result']['report_id']}", headers=alice_research.headers
    ).json()
    assert report["sources"][0]["url"] == A_URL and "forty-two" in report["summary"]
    assert env.fetcher.calls == [(A_URL, 10.0)]
    assert (
        later.delete(f"/api/v1/reports/{report['id']}", headers=alice_research.headers).status_code
        == 204
    )
