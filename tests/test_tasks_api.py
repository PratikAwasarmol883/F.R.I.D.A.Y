import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from friday.domain.task_state import TaskErrorCode, TaskStatus
from friday.persistence.models import Task

from .conftest import BuildApp, Seeded, SessionFactory
from .helpers import audit_rows, count
from .task_helpers import claim_one, fetch, tx

TASKS = "/api/v1/tasks"
S = TaskStatus


def _post(
    client: TestClient, who: Seeded, message: str = "hello", key: str | None = None, **body: Any
) -> Any:
    headers = dict(who.headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    payload = {"type": "assistant.ask", "input": {"message": message}, **body}
    return client.post(TASKS, json=payload, headers=headers)


def _finish(session_factory: SessionFactory, task_id: str, target: S, **kwargs: Any) -> None:
    claimed = claim_one(session_factory)
    assert str(claimed.id) == task_id
    tx(session_factory, lambda r: r.finish(claimed.id, "w1", claimed.attempt, target, **kwargs))


# ----- create -----


def test_create_returns_202_queued_with_location_and_records_event_and_audit(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    response = _post(client, alice, "  What is 2+2?  ")
    assert response.status_code == 202
    body = response.json()
    assert (body["status"], body["type"], body["attempt"], body["result"]) == (
        "queued",
        "assistant.ask",
        0,
        None,
    )
    assert response.headers["Location"] == f"/api/v1/tasks/{body['id']}"
    stored = fetch(session_factory, uuid.UUID(body["id"]))
    assert stored.input == {"message": "What is 2+2?", "conversation_id": None}
    assert stored.device_id == alice.device_id and stored.max_attempts == 3
    [audit] = audit_rows(session_factory, "task.create")
    assert audit.outcome == "success" and audit.target_id == body["id"]


def test_create_requires_authentication(client: TestClient) -> None:
    response = client.post(TASKS, json={"type": "assistant.ask", "input": {"message": "hi"}})
    assert response.status_code == 401 and response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "assistant.ask"},
        {"type": "research", "input": {"message": "x"}},
        {"type": "assistant.ask", "input": {}},
        {"type": "assistant.ask", "input": {"message": ""}},
        {"type": "assistant.ask", "input": {"message": "   "}},
        {"type": "assistant.ask", "input": {"message": "x" * 8001}},
        {"type": "assistant.ask", "input": {"message": "hi", "extra": 1}},
        {
            "type": "assistant.ask",
            "input": {"message": "hi", "conversation_id": "nope-SECRETVALUE"},
        },
        {"type": "assistant.ask", "input": {"message": "hi"}, "extra": 1},
    ],
)
def test_invalid_submissions_are_422_without_echoing_input(
    client: TestClient, alice: Seeded, session_factory: SessionFactory, payload: dict[str, Any]
) -> None:
    response = client.post(TASKS, json=payload, headers=alice.headers)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert "SECRETVALUE" not in response.text
    assert count(session_factory, Task) == 0


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, "semi;colon", "slash/key"])
def test_invalid_idempotency_keys_are_rejected(client: TestClient, alice: Seeded, key: str) -> None:
    headers = {**alice.headers, "Idempotency-Key": key}
    payload = {"type": "assistant.ask", "input": {"message": "hi"}}
    response = client.post(TASKS, json=payload, headers=headers)
    assert response.status_code == 422


def test_conversation_must_belong_to_the_caller(
    client: TestClient, alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    mine = client.post("/api/v1/conversations", json={}, headers=alice.headers).json()["id"]
    body = {"type": "assistant.ask", "input": {"message": "hi", "conversation_id": mine}}
    assert client.post(TASKS, json=body, headers=alice.headers).status_code == 202
    foreign = client.post(TASKS, json=body, headers=bob.headers)
    assert foreign.status_code == 404 and foreign.json()["code"] == "NOT_FOUND"
    missing = {
        "type": "assistant.ask",
        "input": {"message": "hi", "conversation_id": str(uuid.uuid4())},
    }
    assert client.post(TASKS, json=missing, headers=alice.headers).status_code == 404
    assert (
        count(session_factory, Task) == 1
    )  # only the accepted one; both rejected attempts created nothing


# ----- idempotency -----


def test_same_key_and_payload_replays_the_existing_task(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    first = _post(client, alice, "same", key="abc-1")
    second = _post(client, alice, "same", key="abc-1")
    assert (first.status_code, second.status_code) == (202, 200)
    assert first.json()["id"] == second.json()["id"]
    assert count(session_factory, Task) == 1
    assert len(audit_rows(session_factory, "task.create")) == 1


def test_same_key_with_a_different_payload_conflicts(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    _post(client, alice, "one", key="k")
    response = _post(client, alice, "two", key="k")
    assert response.status_code == 409 and response.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert count(session_factory, Task) == 1


def test_keys_are_scoped_per_user_and_absent_keys_never_dedupe(
    client: TestClient, alice: Seeded, bob: Seeded
) -> None:
    a, b = _post(client, alice, key="shared"), _post(client, bob, key="shared")
    assert a.status_code == b.status_code == 202 and a.json()["id"] != b.json()["id"]
    assert _post(client, alice).json()["id"] != _post(client, alice).json()["id"]


def test_ten_concurrent_duplicate_submissions_create_one_task(
    build_app: BuildApp, alice: Seeded, session_factory: SessionFactory
) -> None:
    app = build_app()

    async def scenario() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            return list(
                await asyncio.gather(
                    *[
                        http.post(
                            TASKS,
                            json={"type": "assistant.ask", "input": {"message": "dup"}},
                            headers={**alice.headers, "Idempotency-Key": "race"},
                        )
                        for _ in range(10)
                    ]
                )
            )

    responses = asyncio.run(scenario())
    assert sorted(r.status_code for r in responses) == [200] * 9 + [202]
    assert len({r.json()["id"] for r in responses}) == 1
    assert count(session_factory, Task) == 1


# ----- active-task limit -----


def test_active_task_limit_returns_429_and_is_freed_by_cancel_and_completion(
    make_client: Any, alice: Seeded, session_factory: SessionFactory
) -> None:
    client = make_client(None, max_active_tasks_per_user=2)
    first, second = _post(client, alice, "1", key="a"), _post(client, alice, "2")
    assert (first.status_code, second.status_code) == (202, 202)
    limited = _post(client, alice, "3")
    assert limited.status_code == 429 and limited.json()["code"] == "TASK_LIMIT"
    assert _post(client, alice, "1", key="a").status_code == 200  # replay still works at the limit
    assert count(session_factory, Task) == 2

    assert (
        client.post(f"{TASKS}/{second.json()['id']}/cancel", headers=alice.headers).status_code
        == 200
    )
    assert _post(client, alice, "3").status_code == 202
    _finish(session_factory, first.json()["id"], S.SUCCEEDED, result={})
    assert _post(client, alice, "4").status_code == 202


def test_the_limit_is_per_user(make_client: Any, alice: Seeded, bob: Seeded) -> None:
    client = make_client(None, max_active_tasks_per_user=1)
    assert _post(client, alice).status_code == 202
    assert _post(client, alice).status_code == 429
    assert _post(client, bob).status_code == 202


# ----- read -----


def test_get_is_owner_scoped(client: TestClient, alice: Seeded, bob: Seeded) -> None:
    task_id = _post(client, alice).json()["id"]
    assert client.get(f"{TASKS}/{task_id}", headers=alice.headers).status_code == 200
    foreign = client.get(f"{TASKS}/{task_id}", headers=bob.headers)
    missing = client.get(f"{TASKS}/{uuid.uuid4()}", headers=bob.headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["message"] == missing.json()["message"]
    assert client.get(f"{TASKS}/not-a-uuid", headers=alice.headers).status_code == 422
    assert client.get(f"{TASKS}/{task_id}").status_code == 401


def test_succeeded_task_exposes_answer_sources_and_usage(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    asked = client.post(
        "/api/v1/assistant/ask", json={"message": "seed question"}, headers=alice.headers
    ).json()
    task_id = _post(client, alice).json()["id"]
    result = {
        "conversation_id": asked["conversation_id"],
        "message_id": asked["message_id"],
        "usage": {"input_tokens": 3, "output_tokens": 4},
        "sources": [{"title": "Docs", "url": "https://docs.example"}],
        "tools_used": [{"name": "web_search", "status": "succeeded"}],
    }
    _finish(session_factory, task_id, S.SUCCEEDED, result=result)
    body = client.get(f"{TASKS}/{task_id}", headers=alice.headers).json()
    assert body["status"] == "succeeded" and body["finished_at"] is not None
    assert body["result"]["answer"] == "fake answer"
    assert body["result"]["usage"] == {"input_tokens": 3, "output_tokens": 4}
    assert body["result"]["sources"] == [{"title": "Docs", "url": "https://docs.example"}]
    assert body["error_code"] is None


def test_a_result_pointing_at_another_users_message_never_leaks_its_text(
    client: TestClient, alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    bobs = client.post(
        "/api/v1/assistant/ask", json={"message": "bob private"}, headers=bob.headers
    ).json()
    task_id = _post(client, alice).json()["id"]
    result = {
        "conversation_id": bobs["conversation_id"],
        "message_id": bobs["message_id"],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    _finish(session_factory, task_id, S.SUCCEEDED, result=result)
    body = client.get(f"{TASKS}/{task_id}", headers=alice.headers).json()
    assert body["result"]["answer"] is None and "fake answer" not in str(body)


def test_failed_task_shows_stable_code_and_safe_message_only(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    task_id = _post(client, alice, "secret request text").json()["id"]
    _finish(session_factory, task_id, S.FAILED, error_code=TaskErrorCode.PROVIDER_ERROR)
    response = client.get(f"{TASKS}/{task_id}", headers=alice.headers)
    body = response.json()
    assert (body["status"], body["error_code"]) == ("failed", "provider_error")
    assert body["error_message"] == "The assistant could not complete the request."
    assert "secret request text" not in response.text


# ----- cancel -----


def test_cancel_outcomes_over_http(
    client: TestClient, alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    queued = _post(client, alice).json()["id"]
    response = client.post(f"{TASKS}/{queued}/cancel", headers=alice.headers)
    assert (response.status_code, response.json()["status"]) == (200, "cancelled")
    assert client.post(f"{TASKS}/{queued}/cancel", headers=alice.headers).status_code == 200

    running = _post(client, alice).json()["id"]
    claim_one(session_factory)
    response = client.post(f"{TASKS}/{running}/cancel", headers=alice.headers)
    assert (response.status_code, response.json()["status"]) == (202, "cancel_requested")
    assert response.json()["cancel_requested"] is True
    assert client.post(f"{TASKS}/{running}/cancel", headers=alice.headers).status_code == 200

    done = _post(client, alice).json()["id"]
    _finish(session_factory, done, S.SUCCEEDED, result={})
    conflict = client.post(f"{TASKS}/{done}/cancel", headers=alice.headers)
    assert conflict.status_code == 409 and conflict.json()["code"] == "TASK_NOT_CANCELLABLE"
    assert client.post(f"{TASKS}/{running}/cancel", headers=bob.headers).status_code == 404
    assert len(audit_rows(session_factory, "task.cancel")) == 1  # only the running request


def test_a_client_disconnect_never_cancels_a_task(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    task_id = _post(client, alice).json()["id"]
    client.close()  # the caller goes away without cancelling
    assert fetch(session_factory, uuid.UUID(task_id)).status == "queued"


# ----- events -----


def test_events_are_ordered_paginated_owner_scoped_and_free_of_user_text(
    client: TestClient, alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    task_id = _post(client, alice, "TOPSECRETMESSAGE").json()["id"]
    claimed = claim_one(session_factory)
    tx(session_factory, lambda r: r.release(claimed.id, "w1", claimed.attempt))
    url = f"{TASKS}/{task_id}/events"
    page = client.get(url, headers=alice.headers).json()
    assert [e["event_type"] for e in page["events"]] == ["created", "claimed", "released"]
    assert [e["sequence"] for e in page["events"]] == [1, 2, 3] and page["next_after_sequence"] == 3
    assert "TOPSECRETMESSAGE" not in str(page)

    tail = client.get(url, params={"after_sequence": 1, "limit": 1}, headers=alice.headers).json()
    assert [e["sequence"] for e in tail["events"]] == [2] and tail["next_after_sequence"] == 2
    empty = client.get(url, params={"after_sequence": 3}, headers=alice.headers).json()
    assert empty == {"events": [], "next_after_sequence": 3}

    assert client.get(url, headers=bob.headers).status_code == 404
    for params in ({"limit": 0}, {"limit": 201}, {"after_sequence": -1}):
        assert client.get(url, params=params, headers=alice.headers).status_code == 422


def test_a_retry_pending_task_shows_its_last_error_code(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    task_id = _post(client, alice).json()["id"]
    claimed = claim_one(session_factory)
    tx(
        session_factory,
        lambda r: r.schedule_retry(
            claimed.id,
            "w1",
            claimed.attempt,
            code=TaskErrorCode.PROVIDER_UNAVAILABLE,
            delay_seconds=30,
        ),
    )
    body = client.get(f"{TASKS}/{task_id}", headers=alice.headers).json()
    assert (body["status"], body["attempt"], body["error_code"]) == (
        "queued",
        1,
        "provider_unavailable",
    )
    assert body["error_message"] == "The assistant was temporarily unavailable."
