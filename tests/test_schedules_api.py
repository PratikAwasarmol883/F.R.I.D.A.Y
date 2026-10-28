import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from friday.domain.schedule import ScheduleSpec, next_occurrence, parse_timezone

from .conftest import Seeded, SessionFactory
from .helpers import audit_rows
from .schedule_helpers import new_job, scheduled_task
from .task_helpers import sql, tx

SCHED = "/api/v1/schedules"
DAILY = {"kind": "daily", "time": "07:30"}


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "Morning briefing",
        "task": {"type": "assistant.ask", "input": {"message": "Summarize my day"}},
        "schedule": DAILY,
        "timezone": "Asia/Kolkata",
    }
    data.update(overrides)
    return data


def create(client: TestClient, who: Seeded, **overrides: Any) -> Any:
    return client.post(SCHED, json=body(**overrides), headers=who.headers)


def expected_next(spec: dict[str, Any], zone: str, now: datetime | None = None) -> datetime:
    return next_occurrence(
        ScheduleSpec.model_validate(spec), parse_timezone(zone), now or datetime.now(UTC)
    )


# create / read


def test_create_returns_the_schedule_with_the_correct_next_run(
    client: TestClient, alice: Seeded
) -> None:
    before = datetime.now(UTC)
    response = create(client, alice, name="  Morning briefing  ")
    assert response.status_code == 201
    data = response.json()
    assert (data["name"], data["enabled"], data["timezone"]) == (
        "Morning briefing",
        True,
        "Asia/Kolkata",
    )
    assert data["task"] == {
        "type": "assistant.ask",
        "input": {"message": "Summarize my day", "conversation_id": None},
    }
    assert data["schedule"] == {"kind": "daily", "time": "07:30", "days": []}
    next_run = datetime.fromisoformat(data["next_run_at"])
    assert before < next_run <= before + timedelta(days=1, minutes=1)
    after = datetime.now(UTC)
    assert next_run in {
        expected_next(DAILY, "Asia/Kolkata", before),
        expected_next(DAILY, "Asia/Kolkata", after),
    }  # exact; both bounds are given so an occurrence boundary during the call cannot flake it
    assert data["last_run_at"] is None and data["last_task_id"] is None


def test_list_and_get_are_owner_scoped(client: TestClient, alice: Seeded, bob: Seeded) -> None:
    mine = create(client, alice).json()
    assert [s["id"] for s in client.get(SCHED, headers=alice.headers).json()] == [mine["id"]]
    assert client.get(SCHED, headers=bob.headers).json() == []
    assert client.get(f"{SCHED}/{mine['id']}", headers=alice.headers).status_code == 200
    for method, url, kwargs in [
        ("get", f"{SCHED}/{mine['id']}", {}),
        ("patch", f"{SCHED}/{mine['id']}", {"json": {"name": "x"}}),
        ("delete", f"{SCHED}/{mine['id']}", {}),
        ("get", f"{SCHED}/{mine['id']}/runs", {}),
        ("get", f"{SCHED}/{uuid.uuid4()}", {}),
    ]:
        assert getattr(client, method)(url, headers=bob.headers, **kwargs).status_code == 404
    assert client.get(SCHED).status_code == 401


def test_a_research_schedule_needs_both_research_scopes(
    client: TestClient, alice: Seeded, alice_tools: Seeded, alice_research: Seeded
) -> None:
    research = {
        "type": "research.run",
        "input": {"question": "What is new in Python?", "max_sources": 3},
    }
    for who in (alice, alice_tools):
        denied = create(client, who, task=research)
        assert denied.status_code == 403 and denied.json()["code"] == "FORBIDDEN"
    ok = create(client, alice_research, task=research)
    assert ok.status_code == 201 and ok.json()["task"]["type"] == "research.run"


def test_the_per_user_schedule_limit(make_client: Any, alice: Seeded, bob: Seeded) -> None:
    client = make_client(None, max_schedules_per_user=2)
    assert create(client, alice).status_code == create(client, alice).status_code == 201
    limited = create(client, alice)
    assert limited.status_code == 429 and limited.json()["code"] == "SCHEDULE_LIMIT"
    assert create(client, bob).status_code == 201


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": "Mars/Base"},
        {"timezone": ""},
        {"timezone": "../etc/passwd"},
        {"schedule": {"kind": "daily", "time": "7:30"}},
        {"schedule": {"kind": "daily", "time": "25:00"}},
        {"schedule": {"kind": "hourly", "time": "07:30"}},
        {"schedule": {"kind": "daily", "time": "07:30", "days": ["mon"]}},
        {"schedule": {"kind": "weekly", "time": "07:30"}},
        {"schedule": {"kind": "weekly", "time": "07:30", "days": ["mon", "mon"]}},
        {"name": ""},
        {"name": "x" * 101},
        {"task": {"type": "bogus", "input": {}}},
        {"task": {"type": "assistant.ask", "input": {}}},
        {"task": {"type": "assistant.ask", "input": {"message": ""}}},
        {
            "task": {
                "type": "assistant.ask",
                "input": {"message": "hi", "conversation_id": str(uuid.uuid4())},
            }
        },
        {"task": {"type": "research.run", "input": {"question": ""}}},
        {"task": {"type": "research.run", "input": {"question": "q", "max_sources": 99}}},
        {"enabled": "maybe"},
        {"extra": 1},
    ],
)
def test_invalid_schedules_are_validation_errors(
    client: TestClient, alice: Seeded, session_factory: SessionFactory, overrides: dict[str, Any]
) -> None:
    response = create(client, alice, **overrides)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert client.get(SCHED, headers=alice.headers).json() == []


def test_missing_fields_are_rejected(client: TestClient, alice: Seeded) -> None:
    for field in ("name", "task", "schedule", "timezone"):
        data = body()
        del data[field]
        assert client.post(SCHED, json=data, headers=alice.headers).status_code == 422


# update / delete


def test_updates_recompute_only_when_timing_changes(
    client: TestClient, alice: Seeded, test_db_url: str
) -> None:
    job = create(client, alice).json()
    url = f"{SCHED}/{job['id']}"
    sql(
        test_db_url,
        "UPDATE scheduled_jobs SET next_run_at = '2030-01-01T00:00:00+00' WHERE id = %s",
        (job["id"],),
    )

    renamed = client.patch(url, json={"name": "Renamed"}, headers=alice.headers).json()
    assert renamed["name"] == "Renamed" and renamed["next_run_at"].startswith(
        "2030-01-01"
    )  # untouched

    paused = client.patch(url, json={"enabled": False}, headers=alice.headers).json()
    assert paused["enabled"] is False and paused["next_run_at"].startswith("2030-01-01")

    resumed = client.patch(
        url, json={"enabled": True}, headers=alice.headers
    ).json()  # re-enabling recomputes
    assert resumed["enabled"] is True and datetime.fromisoformat(
        resumed["next_run_at"]
    ) < datetime.now(UTC) + timedelta(days=2)

    sql(
        test_db_url,
        "UPDATE scheduled_jobs SET next_run_at = '2030-01-01T00:00:00+00' WHERE id = %s",
        (job["id"],),
    )
    moved = client.patch(
        url,
        json={"schedule": {"kind": "weekly", "time": "09:00", "days": ["fri"]}},
        headers=alice.headers,
    ).json()
    assert moved["schedule"]["kind"] == "weekly"
    assert (
        datetime.fromisoformat(moved["next_run_at"])
        .astimezone(parse_timezone("Asia/Kolkata"))
        .strftime("%a %H:%M")
        == "Fri 09:00"
    )

    sql(
        test_db_url,
        "UPDATE scheduled_jobs SET next_run_at = '2030-01-01T00:00:00+00' WHERE id = %s",
        (job["id"],),
    )
    zoned = client.patch(url, json={"timezone": "America/New_York"}, headers=alice.headers).json()
    assert (
        datetime.fromisoformat(zoned["next_run_at"])
        .astimezone(parse_timezone("America/New_York"))
        .strftime("%a %H:%M")
        == "Fri 09:00"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"timezone": "Mars/Base"},
        {"schedule": {"kind": "daily", "time": "99:99"}},
        {"name": ""},
        {"extra": 1},
    ],
)
def test_invalid_updates_are_rejected_and_change_nothing(
    client: TestClient, alice: Seeded, payload: dict[str, Any]
) -> None:
    job = create(client, alice).json()
    assert (
        client.patch(f"{SCHED}/{job['id']}", json=payload, headers=alice.headers).status_code == 422
    )
    assert (
        client.get(f"{SCHED}/{job['id']}", headers=alice.headers).json()["name"]
        == "Morning briefing"
    )


def test_delete_removes_the_schedule_and_is_audited(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    job = create(client, alice).json()
    assert client.delete(f"{SCHED}/{job['id']}", headers=alice.headers).status_code == 204
    assert client.get(f"{SCHED}/{job['id']}", headers=alice.headers).status_code == 404
    assert client.delete(f"{SCHED}/{job['id']}", headers=alice.headers).status_code == 404
    actions = {a: audit_rows(session_factory, a) for a in ("schedule.create", "schedule.delete")}
    assert all(len(rows) == 1 and rows[0].target_id == job["id"] for rows in actions.values())


# runs


def test_runs_list_recent_tasks_with_only_result_references(
    client: TestClient, alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice)
    first = tx(
        session_factory,
        lambda r: scheduled_task(r, alice, job, datetime(2026, 10, 28, 7, 30, tzinfo=UTC)),
    )
    second = tx(
        session_factory,
        lambda r: scheduled_task(r, alice, job, datetime(2026, 10, 29, 7, 30, tzinfo=UTC)),
    )
    sql(
        test_db_url,
        "UPDATE tasks SET status = 'succeeded', finished_at = now(), "
        "result = %s::jsonb WHERE id = %s",
        (
            json.dumps({"report_id": "r1", "usage": {"input_tokens": 1}, "sources_count": 2}),
            second.id,
        ),
    )
    runs = client.get(f"{SCHED}/{job.id}/runs", headers=alice.headers).json()
    assert [r["task_id"] for r in runs] == [str(second.id), str(first.id)]  # newest first
    assert runs[0]["status"] == "succeeded" and runs[0]["result"] == {
        "report_id": "r1"
    }  # no usage or counts
    assert runs[1]["status"] == "queued" and runs[1]["result"] is None
    assert client.get(f"{SCHED}/{job.id}/runs", params={"limit": 1}, headers=alice.headers).json()[
        0
    ]["task_id"] == str(second.id)
    for limit in (0, 101):
        assert (
            client.get(
                f"{SCHED}/{job.id}/runs", params={"limit": limit}, headers=alice.headers
            ).status_code
            == 422
        )
