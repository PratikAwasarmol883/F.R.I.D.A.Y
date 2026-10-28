import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from friday.adapters.llm.base import ChatMessage
from friday.adapters.llm.fake import FakeLLMProvider
from friday.adapters.search.base import SearchHit
from friday.adapters.search.fake import FakeSearchProvider
from friday.core.config import Settings
from friday.domain.exceptions import (
    BudgetExceededError,
    DeviceRevokedError,
    InvalidTaskInputError,
    NotFoundError,
    ProviderUnavailableError,
)
from friday.domain.task_state import TaskEventType
from friday.orchestration.tool_registry import ToolRegistry
from friday.persistence.models import Task, UsageRecord
from friday.services.auth_service import AuthService
from friday.workers.context import TaskContext
from friday.workers.errors import LeaseLostError, TaskCancelled
from friday.workers.handlers.assistant_ask import AssistantAskHandler

from .conftest import Seeded, SessionFactory, run_db
from .helpers import audit_rows, count, message_rows, tool_rows
from .task_helpers import claim_one, fetch, new_task, sql, tx
from .tool_helpers import HookedLLM, final, search, search_registry, tool_turn

DOCS = SearchHit("Docs", "https://docs.example/a", "Use lifespan.")


def _run(
    factory: SessionFactory,
    task: Task,
    llm: FakeLLMProvider,
    *,
    registry: ToolRegistry | None = None,
    worker: str = "w1",
    attempt: int | None = None,
    llm_factory: Any = None,
    **settings: Any,
) -> None:
    config = Settings(_env_file=None).model_copy(update=settings)
    handler = AssistantAskHandler(
        factory, config, llm_factory or (lambda: llm), lambda: registry or ToolRegistry()
    )
    ctx = TaskContext(
        task_id=task.id,
        user_id=task.user_id,
        device_id=task.device_id,
        attempt=attempt if attempt is not None else task.attempt,
        worker_id=worker,
        task_input=task.input,
        session_factory=factory,
    )
    asyncio.run(handler.run(ctx))


def _claimed(factory: SessionFactory, who: Seeded, **kwargs: Any) -> Task:
    new_task(factory, who, **kwargs)
    return claim_one(factory)


def _request_cancel(db_url: str, task_id: uuid.UUID) -> None:
    sql(
        db_url,
        "UPDATE tasks SET status = 'cancel_requested', cancel_requested_at = now() WHERE id = %s",
        (task_id,),
    )


def _events(factory: SessionFactory, task_id: uuid.UUID) -> list[Any]:
    return tx(factory, lambda r: r.events_after(task_id, 0, 100))


# ----- success -----


def test_success_is_atomic_and_fully_recorded(
    alice_tools: Seeded, session_factory: SessionFactory
) -> None:
    task = _claimed(session_factory, alice_tools, message="How do I start up?")
    provider = FakeSearchProvider(hits=[DOCS])
    llm = FakeLLMProvider(script=[tool_turn(search("fastapi lifespan")), final("Use lifespan.")])
    _run(session_factory, task, llm, registry=search_registry(provider))

    done = fetch(session_factory, task.id)
    assert (done.status, done.error_code) == ("succeeded", None)
    result = done.result
    assert result is not None
    assert result["usage"] == {"input_tokens": 20, "output_tokens": 10}
    assert result["sources"] == [{"title": "Docs", "url": "https://docs.example/a"}]
    assert result["tools_used"] == [{"name": "web_search", "status": "succeeded"}]
    assert "Use lifespan." not in str(result)  # the answer text stays in `messages`

    user_msg, reply = message_rows(session_factory)
    assert (user_msg.content, reply.content) == ("How do I start up?", "Use lifespan.")
    assert str(reply.id) == result["message_id"]

    [execution] = tool_rows(session_factory)
    assert execution.task_id == task.id and str(execution.message_id) == result["message_id"]

    events = _events(session_factory, task.id)
    assert [e.event_type for e in events] == ["created", "claimed"] + ["progress"] * 3 + [
        "succeeded"
    ]
    assert [e.payload["step"] for e in events if e.event_type == "progress"] == [
        "model_call",
        "tool_call",
        "model_call",
    ]
    assert events[3].payload == {"step": "tool_call", "tool": "web_search", "status": "succeeded"}
    assert "How do I start up?" not in str([e.payload for e in events])

    assert [a.outcome for a in audit_rows(session_factory, "task.finish")] == ["success"]
    assert [a.outcome for a in audit_rows(session_factory, "assistant.ask")] == ["success"]
    assert count(session_factory, UsageRecord) == 1


def test_continuing_a_conversation_sends_its_history(
    alice: Seeded, client_ask: tuple[str, str], session_factory: SessionFactory
) -> None:
    conversation_id, _ = client_ask
    task = _claimed(
        session_factory,
        alice,
        task_input={"message": "follow-up", "conversation_id": conversation_id},
    )
    llm = FakeLLMProvider()
    _run(session_factory, task, llm)
    assert llm.calls[0] == [
        ChatMessage("user", "seed"),
        ChatMessage("assistant", "fake answer"),
        ChatMessage("user", "follow-up"),
    ]
    assert fetch(session_factory, task.id).result["conversation_id"] == conversation_id  # type: ignore[index]


# ----- fencing: a stale worker can never leave duplicate ef -----


def test_a_stale_context_cannot_start_emit_or_write(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = _claimed(session_factory, alice)
    llm = FakeLLMProvider()
    for worker, attempt in [("intruder", task.attempt), ("w1", task.attempt + 5)]:
        with pytest.raises(LeaseLostError):
            _run(session_factory, task, llm, worker=worker, attempt=attempt)
    assert llm.calls == [] and message_rows(session_factory) == []
    assert fetch(session_factory, task.id).status == "running"


def test_lease_stolen_during_the_model_call_writes_nothing(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = _claimed(session_factory, alice)

    def steal(call_index: int) -> None:
        sql(
            test_db_url,
            "UPDATE tasks SET lease_owner = 'w2', attempt = attempt + 1 WHERE id = %s",
            (task.id,),
        )

    llm = HookedLLM(steal, script=[final("late answer")])
    with pytest.raises(LeaseLostError):
        _run(session_factory, task, llm)
    stolen = fetch(session_factory, task.id)
    assert (stolen.status, stolen.lease_owner, stolen.attempt, stolen.result) == (
        "running",
        "w2",
        2,
        None,
    )
    assert message_rows(session_factory) == [] and count(session_factory, UsageRecord) == 0
    assert audit_rows(session_factory, "task.finish") == []


def test_lease_reaped_and_reclaimed_during_the_call_still_writes_nothing(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = _claimed(session_factory, alice)

    def reap_and_reclaim(call_index: int) -> None:
        sql(
            test_db_url,
            "UPDATE tasks SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL "
            "WHERE id = %s",
            (task.id,),
        )
        sql(
            test_db_url,
            "UPDATE tasks SET status = 'running', lease_owner = 'w2', attempt = attempt + 1, "
            "lease_expires_at = now() + interval '60 seconds' WHERE id = %s",
            (task.id,),
        )

    llm = HookedLLM(reap_and_reclaim, script=[final("late answer")])
    with pytest.raises(LeaseLostError):
        _run(session_factory, task, llm)
    assert message_rows(session_factory) == []
    assert fetch(session_factory, task.id).lease_owner == "w2"


# ----- cancellation -----


def test_cancel_before_start_never_calls_the_model(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = _claimed(session_factory, alice)
    tx(session_factory, lambda r: r.request_cancel(task.id, alice.user_id))
    llm = FakeLLMProvider()
    with pytest.raises(TaskCancelled):
        _run(session_factory, task, llm)
    assert llm.calls == [] and message_rows(session_factory) == []


def test_cancel_during_the_model_call_prevents_any_write(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = _claimed(session_factory, alice)
    llm = HookedLLM(
        lambda i: _request_cancel(test_db_url, task.id), script=[final("answer nobody asked for")]
    )
    with pytest.raises(TaskCancelled):
        _run(session_factory, task, llm)
    assert message_rows(session_factory) == [] and count(session_factory, UsageRecord) == 0
    assert fetch(session_factory, task.id).status == "cancel_requested"


def test_cancel_between_the_model_call_and_the_tool_call_skips_the_tool(
    alice_tools: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = _claimed(session_factory, alice_tools)
    provider = FakeSearchProvider(hits=[DOCS])
    llm = HookedLLM(
        lambda i: _request_cancel(test_db_url, task.id) if i == 0 else None,
        script=[tool_turn(search("never runs")), final("x")],
    )
    with pytest.raises(TaskCancelled):
        _run(session_factory, task, llm, registry=search_registry(provider))
    assert provider.calls == [] and tool_rows(session_factory) == []


# ----- authorization is evaluated at execution time -----


def test_revoked_or_missing_device_never_runs(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = _claimed(session_factory, alice)

    async def revoke(session: Any) -> None:
        await AuthService(session).revoke_device(alice.device_id)

    run_db(session_factory, revoke)
    llm = FakeLLMProvider()
    with pytest.raises(DeviceRevokedError):
        _run(session_factory, task, llm)
    assert llm.calls == []

    orphan = SimpleNamespace(
        id=task.id, user_id=task.user_id, device_id=None, attempt=task.attempt, input=task.input
    )
    with pytest.raises(DeviceRevokedError):
        _run(session_factory, orphan, llm)  # type: ignore[arg-type]


def test_scopes_are_read_at_execution_time_not_snapshotted(
    alice_tools: Seeded, session_factory: SessionFactory
) -> None:
    registry = search_registry(FakeSearchProvider())

    first = _claimed(session_factory, alice_tools)

    async def remove() -> None:
        async with session_factory() as session:
            await AuthService(session).set_device_scopes(alice_tools.device_id, [])

    asyncio.run(remove())
    llm = FakeLLMProvider()
    _run(session_factory, first, llm, registry=registry)
    assert llm.requests[0].tools == ()  # scope removed after creation: no tools offered

    async def grant() -> None:
        async with session_factory() as session:
            await AuthService(session).set_device_scopes(alice_tools.device_id, ["tool:web_search"])

    asyncio.run(grant())
    second = _claimed(session_factory, alice_tools)
    llm = FakeLLMProvider()
    _run(session_factory, second, llm, registry=registry)
    assert [t.name for t in llm.requests[0].tools] == ["web_search"]


# ----- errors map cleanly -----


def test_provider_failure_after_a_tool_call_keeps_tool_audit_but_writes_no_answer(
    alice_tools: Seeded, session_factory: SessionFactory
) -> None:
    task = _claimed(session_factory, alice_tools)
    llm = FakeLLMProvider(script=[tool_turn(search()), ProviderUnavailableError()])
    with pytest.raises(ProviderUnavailableError):
        _run(session_factory, task, llm, registry=search_registry(FakeSearchProvider()))
    [execution] = tool_rows(session_factory)
    assert execution.task_id == task.id and execution.message_id is None
    assert message_rows(session_factory) == []
    assert (
        fetch(session_factory, task.id).status == "running"
    )  # the runner decides what happens next


def test_budget_unknown_conversation_invalid_input_and_missing_key(
    alice: Seeded, bob: Seeded, client_ask: tuple[str, str], session_factory: SessionFactory
) -> None:
    llm = FakeLLMProvider()
    budget_task = _claimed(session_factory, alice)
    with pytest.raises(BudgetExceededError):
        _run(session_factory, budget_task, llm, daily_token_budget=15)  # the seed ask used 15

    foreign = _claimed(
        session_factory, bob, task_input={"message": "hi", "conversation_id": client_ask[0]}
    )
    with pytest.raises(NotFoundError):
        _run(session_factory, foreign, llm)

    invalid = _claimed(session_factory, alice, task_input={"message": ""})
    with pytest.raises(InvalidTaskInputError):
        _run(session_factory, invalid, llm)

    no_key = _claimed(session_factory, alice)

    def missing_key() -> FakeLLMProvider:
        raise ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError):
        _run(session_factory, no_key, llm, llm_factory=missing_key)
    assert llm.calls == []


def test_progress_events_are_fenced_too(alice: Seeded, session_factory: SessionFactory) -> None:
    task = _claimed(session_factory, alice)
    ctx = TaskContext(
        task_id=task.id,
        user_id=alice.user_id,
        device_id=alice.device_id,
        attempt=task.attempt + 1,
        worker_id="w1",
        task_input={},
        session_factory=session_factory,
    )
    with pytest.raises(LeaseLostError):
        asyncio.run(ctx.emit("model_call", {"step": 1}))
    kinds = [e.event_type for e in _events(session_factory, task.id)]
    assert TaskEventType.PROGRESS.value not in kinds
