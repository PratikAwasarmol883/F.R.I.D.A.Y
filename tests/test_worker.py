import asyncio
import os
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from friday.adapters.llm.fake import FakeLLMProvider
from friday.core.config import Settings
from friday.domain.exceptions import (
    BudgetExceededError,
    ProviderError,
    ProviderUnavailableError,
    TaskNotCancellableError,
)
from friday.domain.task_state import TaskStatus
from friday.orchestration.tool_registry import ToolRegistry
from friday.services.auth_service import AuthService
from friday.workers.context import TaskContext
from friday.workers.handlers.assistant_ask import AssistantAskHandler
from friday.workers.worker import Worker

from .conftest import BuildApp, Seeded, SessionFactory, run_db
from .helpers import audit_rows, message_rows
from .task_helpers import LEASE, atx, fetch, new_task, sql, tx
from .worker_handlers import ScriptedHandler, registry_for, succeed

ROOT = Path(__file__).resolve().parents[1]
FAST: dict[str, Any] = {
    "task_poll_interval_seconds": 0.02,
    "task_heartbeat_seconds": 0.05,
    "task_lease_seconds": 0.5,
    "task_retry_base_seconds": 0.02,
    "task_retry_max_seconds": 0.05,
    "task_shutdown_grace_seconds": 2.0,
}
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled"}


def settings_for(**overrides: Any) -> Settings:
    return Settings(_env_file=None).model_copy(update={**FAST, **overrides})


async def wait_until(predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not reached in time")
        await asyncio.sleep(0.02)


def drive(
    factory: SessionFactory,
    handlers: Any,
    scenario: Callable[[list[Worker]], Awaitable[None]],
    *,
    settings: Settings | None = None,
    workers: int = 1,
) -> list[Worker]:
    """Run worker loop(s) while `scenario` observes and pokes at the system, then stop them."""

    async def main() -> list[Worker]:
        cfg = settings or settings_for()
        pool = [Worker(factory, cfg, handlers, worker_id=f"w{i}") for i in range(workers)]
        loops = [asyncio.create_task(w.run()) for w in pool]
        try:
            await scenario(pool)
        finally:
            for w in pool:
                w.request_stop()
            await asyncio.gather(*loops)
        return pool

    return asyncio.run(main())


def state(db_url: str, task_id: Any) -> tuple[str, int, str | None]:
    return sql(db_url, "SELECT status, attempt, error_code FROM tasks WHERE id = %s", (task_id,))[0]


def event_types(factory: SessionFactory, task_id: Any) -> list[str]:
    return [e.event_type for e in tx(factory, lambda r: r.events_after(task_id, 0, 100))]


def real_handler(factory: SessionFactory, llm: FakeLLMProvider) -> AssistantAskHandler:
    return AssistantAskHandler(factory, settings_for(), lambda: llm, lambda: ToolRegistry())


# ----- end to end -----


def test_a_task_completes_after_the_client_disconnects_and_the_result_is_fetchable_later(
    build_app: BuildApp,
    make_client: Any,
    alice: Seeded,
    session_factory: SessionFactory,
    test_db_url: str,
) -> None:
    submit = make_client(None)
    created = submit.post(
        "/api/v1/tasks",
        json={"type": "assistant.ask", "input": {"message": "What is 2+2?"}},
        headers=alice.headers,
    )
    task_id = created.json()["id"]
    submit.close()  # the caller disconnects

    handlers = registry_for(real_handler(session_factory, FakeLLMProvider(answer="Four.")))

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task_id)[0] in TERMINAL)

    drive(session_factory, handlers, scenario)

    later = make_client(None)  # a fresh connection, possibly from another device
    body = later.get(f"/api/v1/tasks/{task_id}", headers=alice.headers).json()
    assert body["status"] == "succeeded" and body["attempt"] == 1
    assert body["result"]["answer"] == "Four."
    assert body["result"]["usage"] == {"input_tokens": 10, "output_tokens": 5}
    types = event_types(session_factory, task_id)
    assert types[0] == "created" and types[-1] == "succeeded" and "claimed" in types
    assert len(message_rows(session_factory)) == 2


# ----- retries, failures, timeouts -----


def test_transient_failures_are_retried_with_backoff_until_success(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)

    async def flaky(ctx: TaskContext) -> None:
        if ctx.attempt < 3:
            raise ProviderUnavailableError
        await succeed(ctx, session_factory)

    handler = ScriptedHandler(flaky)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("succeeded", 3, None)
    assert [attempt for _, attempt in handler.runs] == [1, 2, 3]
    retries = [
        e
        for e in tx(session_factory, lambda r: r.events_after(task.id, 0, 100))
        if e.event_type == "retry_scheduled"
    ]
    assert [e.payload["code"] for e in retries] == ["provider_unavailable"] * 2
    assert all(0.01 <= e.payload["delay_seconds"] <= 0.05 for e in retries)


def test_retries_are_bounded_by_max_attempts(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice, max_attempts=3)

    async def always(ctx: TaskContext) -> None:
        raise ProviderUnavailableError

    handler = ScriptedHandler(always)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("failed", 3, "provider_unavailable")
    assert len(handler.runs) == 3 and event_types(session_factory, task.id)[-1] == "failed"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ProviderError(), "provider_error"),
        (BudgetExceededError(), "budget_exceeded"),
        (RuntimeError("secret internal detail"), "internal_error"),
    ],
)
def test_non_retryable_failures_fail_immediately_with_a_stable_code(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str, error: Exception, code: str
) -> None:
    task = new_task(session_factory, alice)

    async def fail(ctx: TaskContext) -> None:
        raise error

    handler = ScriptedHandler(fail)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("failed", 1, code)
    assert len(handler.runs) == 1
    events = tx(session_factory, lambda r: r.events_after(task.id, 0, 100))
    assert "secret internal detail" not in str([e.payload for e in events])


def test_runtime_limit_times_the_task_out_without_retrying(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    sql(test_db_url, "UPDATE tasks SET max_runtime_seconds = 1 WHERE id = %s", (task.id,))
    interrupted: list[bool] = []

    async def stuck(ctx: TaskContext) -> None:
        try:
            await asyncio.sleep(30)
        finally:
            interrupted.append(True)

    handler = ScriptedHandler(stuck)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("timed_out", 1, "timed_out")
    assert interrupted == [True] and len(handler.runs) == 1


def test_a_handler_that_forgets_to_finish_is_failed_not_left_running(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)

    async def forgetful(ctx: TaskContext) -> None:
        return None

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(ScriptedHandler(forgetful)), scenario)
    assert state(test_db_url, task.id) == ("failed", 1, "internal_error")


def test_unknown_task_types_fail_with_invalid_input(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    sql(test_db_url, "UPDATE tasks SET type = 'bogus' WHERE id = %s", (task.id,))

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    handler = ScriptedHandler(lambda ctx: succeed(ctx, session_factory))
    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("failed", 1, "invalid_input") and handler.runs == []


# ----- cancellation -----


def test_cancelling_a_running_task_interrupts_the_handler(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    interrupted: list[bool] = []

    async def stuck(ctx: TaskContext) -> None:
        try:
            await asyncio.sleep(30)
        finally:
            interrupted.append(True)

    handler = ScriptedHandler(stuck)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: len(handler.runs) == 1)
        await atx(session_factory, lambda r: r.request_cancel(task.id, alice.user_id))
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL, timeout=5)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("cancelled", 1, None) and interrupted == [True]
    types = event_types(session_factory, task.id)
    assert types[-2:] == ["cancel_requested", "cancelled"]
    assert fetch(session_factory, task.id).result is None


@pytest.mark.parametrize("cancel_first", [True, False])
def test_cancel_racing_with_completion_has_exactly_one_winner(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str, cancel_first: bool
) -> None:
    task = new_task(session_factory, alice)
    outcome: dict[str, Any] = {}

    async def finishing(ctx: TaskContext) -> None:
        if cancel_first:
            await asyncio.sleep(0.3)  # the cancel lands before the handler reaches its commit
        await succeed(ctx, session_factory, hold_seconds=0.3)

    handler = ScriptedHandler(finishing)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: len(handler.runs) == 1)
        await asyncio.sleep(0.1 if cancel_first else 0.45)  # second case: tx holds the row lock
        try:
            result = await atx(session_factory, lambda r: r.request_cancel(task.id, alice.user_id))
            outcome["cancel"] = result.outcome
        except TaskNotCancellableError:
            outcome["cancel"] = "too_late"
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL, timeout=5)

    drive(session_factory, registry_for(handler), scenario)
    status = state(test_db_url, task.id)[0]
    if cancel_first:
        assert (outcome["cancel"], status) == ("cancel_requested", "cancelled")
        assert fetch(session_factory, task.id).result is None
    else:
        assert (outcome["cancel"], status) == ("too_late", "succeeded")
        assert fetch(session_factory, task.id).result == {"ok": True}


# ----- leases, recovery, fencing -----


def test_heartbeats_keep_a_slow_task_from_being_reaped(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)

    async def slow(ctx: TaskContext) -> None:
        await asyncio.sleep(1.5)  # three times the lease
        await succeed(ctx, session_factory)

    handler = ScriptedHandler(slow)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("succeeded", 1, None)
    assert "lease_expired_requeued" not in event_types(session_factory, task.id)


def test_a_stolen_lease_makes_the_worker_abandon_the_task_without_writing(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    interrupted: list[bool] = []

    async def stuck(ctx: TaskContext) -> None:
        try:
            await asyncio.sleep(30)
        finally:
            interrupted.append(True)

    handler = ScriptedHandler(stuck)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: len(handler.runs) == 1)
        sql(
            test_db_url,
            "UPDATE tasks SET lease_owner = 'thief', attempt = attempt + 1, "
            "lease_expires_at = now() + interval '1 hour' WHERE id = %s",
            (task.id,),
        )
        await wait_until(lambda: interrupted == [True], timeout=5)

    drive(session_factory, registry_for(handler), scenario)
    row = fetch(session_factory, task.id)
    assert (row.status, row.lease_owner, row.attempt, row.result) == ("running", "thief", 2, None)
    assert audit_rows(session_factory, "task.finish") == []


def test_the_worker_reaper_recovers_a_task_abandoned_by_a_dead_worker(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    tx(session_factory, lambda r: r.claim("ghost", 1, LEASE))  # claimed, then the ghost "died"
    sql(
        test_db_url,
        "UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        (task.id,),
    )
    handler = ScriptedHandler(lambda ctx: succeed(ctx, session_factory))

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("succeeded", 2, None)
    types = event_types(session_factory, task.id)
    assert "lease_expired_requeued" in types and types.count("claimed") == 2


def test_a_killed_worker_process_loses_nothing_and_the_task_completes_exactly_once(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice, message="survive the crash")
    env = {
        **os.environ,
        "DATABASE_URL": test_db_url,
        "TASK_LEASE_SECONDS": "1",
        "TASK_HEARTBEAT_SECONDS": "0.2",
        "TASK_POLL_INTERVAL_SECONDS": "0.1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.worker_crash_script"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    watchdog = threading.Timer(40, proc.kill)
    watchdog.start()
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        assert line.startswith("RUNNING"), f"worker did not start a task: {line!r}"
        proc.kill()  # SIGKILL: no cleanup, no release, no goodbye
        proc.wait()
    finally:
        watchdog.cancel()
    assert state(test_db_url, task.id)[:2] == ("running", 1)
    assert fetch(session_factory, task.id).lease_owner == "doomed-worker"

    survivor = real_handler(session_factory, FakeLLMProvider(answer="recovered"))

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL, timeout=20)

    drive(session_factory, registry_for(survivor), scenario)
    assert state(test_db_url, task.id) == ("succeeded", 2, None)
    assert [m.content for m in message_rows(session_factory)] == ["survive the crash", "recovered"]
    types = event_types(session_factory, task.id)
    assert "lease_expired_requeued" in types and types.count("claimed") == 2
    assert [a.outcome for a in audit_rows(session_factory, "task.finish")] == ["success"]


# ----- authorization at claim time -----


def test_a_revoked_devices_queued_task_is_cancelled_and_never_runs(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)

    async def revoke(session: Any) -> None:
        await AuthService(session).revoke_device(alice.device_id)

    run_db(session_factory, revoke)
    handler = ScriptedHandler(lambda ctx: succeed(ctx, session_factory))

    async def scenario(_: list[Worker]) -> None:
        await wait_until(lambda: state(test_db_url, task.id)[0] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario)
    assert state(test_db_url, task.id) == ("cancelled", 1, "device_revoked") and handler.runs == []
    assert [a.outcome for a in audit_rows(session_factory, "task.finish")] == ["denied"]


# ----- shutdown -----


def test_shutdown_within_the_grace_period_lets_the_task_finish(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)

    async def brief(ctx: TaskContext) -> None:
        await asyncio.sleep(0.3)
        await succeed(ctx, session_factory)

    handler = ScriptedHandler(brief)

    async def scenario(ws: list[Worker]) -> None:
        await wait_until(lambda: len(handler.runs) == 1)
        ws[0].request_stop()

    drive(
        session_factory,
        registry_for(handler),
        scenario,
        settings=settings_for(task_shutdown_grace_seconds=5),
    )
    assert state(test_db_url, task.id) == ("succeeded", 1, None)


def test_shutdown_beyond_the_grace_period_releases_the_task_without_using_an_attempt(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    interrupted: list[bool] = []

    async def stuck(ctx: TaskContext) -> None:
        try:
            await asyncio.sleep(30)
        finally:
            interrupted.append(True)

    handler = ScriptedHandler(stuck)

    async def scenario(ws: list[Worker]) -> None:
        await wait_until(lambda: len(handler.runs) == 1)
        ws[0].request_stop()

    drive(
        session_factory,
        registry_for(handler),
        scenario,
        settings=settings_for(task_shutdown_grace_seconds=0.1),
    )
    assert interrupted == [True]
    assert state(test_db_url, task.id) == ("queued", 0, None)
    assert event_types(session_factory, task.id)[-1] == "released"


# ----- capacity and fairness of claims -----


@pytest.mark.parametrize(("concurrency", "expected_peak"), [(1, 1), (3, 3)])
def test_concurrency_limit_bounds_parallel_tasks(
    alice: Seeded,
    session_factory: SessionFactory,
    test_db_url: str,
    concurrency: int,
    expected_peak: int,
) -> None:
    tasks = [new_task(session_factory, alice) for _ in range(4)]
    live, peak = 0, 0

    async def work(ctx: TaskContext) -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.2)
        live -= 1
        await succeed(ctx, session_factory)

    handler = ScriptedHandler(work)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(
            lambda: all(state(test_db_url, t.id)[0] in TERMINAL for t in tasks), timeout=20
        )

    drive(
        session_factory,
        registry_for(handler),
        scenario,
        settings=settings_for(task_worker_concurrency=concurrency),
    )
    assert peak == expected_peak
    assert all(state(test_db_url, t.id)[0] == "succeeded" for t in tasks)


def test_two_workers_never_run_the_same_task_twice(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    tasks = [new_task(session_factory, alice) for _ in range(8)]

    async def work(ctx: TaskContext) -> None:
        await asyncio.sleep(0.05)
        await succeed(ctx, session_factory)

    handler = ScriptedHandler(work)

    async def scenario(_: list[Worker]) -> None:
        await wait_until(
            lambda: all(state(test_db_url, t.id)[0] in TERMINAL for t in tasks), timeout=20
        )

    drive(session_factory, registry_for(handler), scenario, workers=2)
    assert sorted(handler.runs) == sorted(
        (t.id, 1) for t in tasks
    )  # each exactly once, first attempt


def test_worker_purge_removes_only_old_finished_tasks(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    old, fresh = new_task(session_factory, alice), new_task(session_factory, alice)
    tx(session_factory, lambda r: r.claim("w", 2, LEASE))
    for task in (old, fresh):
        tx(session_factory, lambda r: r.finish(task.id, "w", 1, TaskStatus.SUCCEEDED, result={}))
    sql(
        test_db_url,
        "UPDATE tasks SET finished_at = now() - interval '40 days' WHERE id = %s",
        (old.id,),
    )

    async def scenario(ws: list[Worker]) -> None:
        assert await ws[0].purge() == 1

    drive(
        session_factory,
        registry_for(ScriptedHandler(lambda ctx: succeed(ctx, session_factory))),
        scenario,
    )
    assert {row[0] for row in sql(test_db_url, "SELECT id FROM tasks")} == {fresh.id}
