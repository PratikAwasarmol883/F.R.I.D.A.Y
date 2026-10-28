import uuid
from datetime import timedelta

import pytest

from friday.domain.exceptions import NotFoundError, TaskNotCancellableError
from friday.domain.task_state import (
    InvalidTransitionError,
    TaskErrorCode,
    TaskStatus,
)

from .conftest import Seeded, SessionFactory
from .helpers import audit_rows
from .task_helpers import (
    LEASE,
    claim_one,
    concurrently,
    expire_lease,
    fetch,
    make_due,
    new_task,
    sql,
    status_of,
    tx,
)

S = TaskStatus


def _reap(factory: SessionFactory, limit: int = 10):  # type: ignore[no-untyped-def]
    return tx(factory, lambda repo: repo.reap_expired(limit, retry_base=5, retry_cap=120))


# ----- creation and idempotency -----


def test_create_starts_queued_with_a_created_event(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = new_task(session_factory, alice)
    assert (task.status, task.attempt, task.event_seq) == ("queued", 0, 1)
    [event] = tx(session_factory, lambda r: r.events_after(task.id, 0, 10))
    assert (event.sequence, event.event_type) == (1, "created")


def test_same_idempotency_key_returns_the_existing_task(
    alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    first = new_task(session_factory, alice, key="k1")

    async def again(repo):  # type: ignore[no-untyped-def]
        return await repo.create(
            user_id=alice.user_id,
            device_id=alice.device_id,
            task_type="assistant.ask",
            task_input={"message": "other"},
            idempotency_key="k1",
            fingerprint="f" * 64,
            max_attempts=3,
            max_runtime_seconds=300,
        )

    second, created = tx(session_factory, again)
    assert created is False and second.id == first.id
    other_user = new_task(session_factory, bob, key="k1")  # keys are per user
    assert other_user.id != first.id
    no_key_a, no_key_b = new_task(session_factory, alice), new_task(session_factory, alice)
    assert no_key_a.id != no_key_b.id


def test_concurrent_duplicate_submissions_create_exactly_one_task(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    async def submit(repo, index):  # type: ignore[no-untyped-def]
        return await repo.create(
            user_id=alice.user_id,
            device_id=alice.device_id,
            task_type="assistant.ask",
            task_input={"message": "x"},
            idempotency_key="dup",
            fingerprint="a" * 64,
            max_attempts=3,
            max_runtime_seconds=300,
        )

    results = concurrently(session_factory, 10, submit)
    assert len({task.id for task, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1
    assert sql(test_db_url, "SELECT count(*) FROM tasks")[0][0] == 1


def test_count_active_ignores_terminal_tasks(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    a, _ = new_task(session_factory, alice), new_task(session_factory, alice)
    assert tx(session_factory, lambda r: r.count_active(alice.user_id)) == 2
    tx(session_factory, lambda r: r.request_cancel(a.id, alice.user_id))
    assert tx(session_factory, lambda r: r.count_active(alice.user_id)) == 1


# ----- claiming -----


def test_claim_sets_lease_fields_and_orders_by_age(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    first, second = new_task(session_factory, alice), new_task(session_factory, alice)
    [claimed] = tx(session_factory, lambda r: r.claim("w1", 1, LEASE))
    assert claimed.id == first.id
    assert (claimed.status, claimed.attempt, claimed.lease_owner) == ("running", 1, "w1")
    assert claimed.lease_expires_at is not None and claimed.started_at is not None
    assert status_of(session_factory, second.id) is S.QUEUED
    events = tx(session_factory, lambda r: r.events_after(first.id, 0, 10))
    assert [e.event_type for e in events] == ["created", "claimed"]


def test_claim_respects_run_after_and_limit(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    later = new_task(session_factory, alice)
    sql(
        test_db_url,
        "UPDATE tasks SET run_after = now() + interval '1 hour' WHERE id = %s",
        (later.id,),
    )
    assert tx(session_factory, lambda r: r.claim("w1", 5, LEASE)) == []
    due = [new_task(session_factory, alice) for _ in range(3)]
    claimed = tx(session_factory, lambda r: r.claim("w1", 2, LEASE))
    assert {t.id for t in claimed} == {
        t.id for t in due[:2]
    }  # the oldest two; RETURNING is unordered
    assert tx(session_factory, lambda r: r.claim("w1", 0, LEASE)) == []


def test_concurrent_workers_never_claim_the_same_task(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    ids = {new_task(session_factory, alice).id for _ in range(12)}
    batches = concurrently(session_factory, 6, lambda repo, i: repo.claim(f"worker-{i}", 4, LEASE))
    claimed = [task.id for batch in batches for task in batch]
    assert len(claimed) == len(set(claimed)) == 12 and set(claimed) == ids


# ----- heartbeat and fencing -----


def test_heartbeat_extends_the_lease_and_reports_cancellation(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    claimed = claim_one(session_factory)
    sql(
        test_db_url,
        "UPDATE tasks SET lease_expires_at = now() + interval '1 second' WHERE id = %s",
        (task.id,),
    )
    beat = tx(session_factory, lambda r: r.heartbeat(task.id, "w1", claimed.attempt, LEASE))
    assert beat is not None and beat.cancel_requested is False
    assert fetch(session_factory, task.id).lease_expires_at > claimed.lease_expires_at - timedelta(
        seconds=5
    )  # type: ignore[operator]
    tx(session_factory, lambda r: r.request_cancel(task.id, alice.user_id))
    beat = tx(session_factory, lambda r: r.heartbeat(task.id, "w1", claimed.attempt, LEASE))
    assert beat is not None and beat.cancel_requested is True


def test_wrong_owner_or_attempt_cannot_write_anything(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = new_task(session_factory, alice)
    claim_one(session_factory)
    for owner, attempt in [("intruder", 1), ("w1", 2), ("w1", 0)]:
        assert tx(session_factory, lambda r: r.heartbeat(task.id, owner, attempt, LEASE)) is None
        assert tx(session_factory, lambda r: r.lock_fenced(task.id, owner, attempt)) is None
        assert tx(session_factory, lambda r: r.finish(task.id, owner, attempt, S.SUCCEEDED)) is None
        retry = tx(
            session_factory,
            lambda r: r.schedule_retry(
                task.id, owner, attempt, code=TaskErrorCode.PROVIDER_TIMEOUT, delay_seconds=1
            ),
        )
        assert retry is None
        assert tx(session_factory, lambda r: r.release(task.id, owner, attempt)) is None
    assert status_of(session_factory, task.id) is S.RUNNING


def test_stale_worker_is_fenced_out_after_reap_and_reclaim(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    claim_one(session_factory, "w1")
    expire_lease(test_db_url, task.id)
    [reaped] = _reap(session_factory)
    assert (reaped.task_id, reaped.outcome) == (task.id, "requeued")
    requeued = fetch(session_factory, task.id)
    assert (requeued.status, requeued.attempt, requeued.error_code) == ("queued", 1, "worker_lost")
    assert requeued.lease_owner is None and requeued.run_after > requeued.updated_at

    make_due(test_db_url, task.id)
    [again] = tx(session_factory, lambda r: r.claim("w2", 1, LEASE))
    assert (again.attempt, again.lease_owner) == (2, "w2")

    # w1 still believes it holds attempt 1 and tries everything.
    assert tx(session_factory, lambda r: r.heartbeat(task.id, "w1", 1, LEASE)) is None
    assert (
        tx(session_factory, lambda r: r.finish(task.id, "w1", 1, S.SUCCEEDED, result={"x": 1}))
        is None
    )
    assert tx(session_factory, lambda r: r.heartbeat(task.id, "w2", 2, LEASE)) is not None
    final = fetch(session_factory, task.id)
    assert (final.status, final.lease_owner, final.result) == ("running", "w2", None)

    # Same worker id reclaiming still gets a new attempt number, so the old fence stays dead.
    expire_lease(test_db_url, task.id)
    _reap(session_factory)
    make_due(test_db_url, task.id)
    [third] = tx(session_factory, lambda r: r.claim("w2", 1, LEASE))
    assert third.attempt == 3
    assert tx(session_factory, lambda r: r.heartbeat(task.id, "w2", 2, LEASE)) is None


# ----- finishing -----


def test_finish_succeeded_records_result_event_and_audit(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = new_task(session_factory, alice)
    claim_one(session_factory)
    outcome = tx(
        session_factory, lambda r: r.finish(task.id, "w1", 1, S.SUCCEEDED, result={"a": 1})
    )
    assert outcome is S.SUCCEEDED
    done = fetch(session_factory, task.id)
    assert (done.status, done.result, done.error_code) == ("succeeded", {"a": 1}, None)
    assert done.finished_at is not None and done.lease_expires_at is None
    assert [a.outcome for a in audit_rows(session_factory, "task.finish")] == ["success"]
    assert (
        tx(session_factory, lambda r: r.finish(task.id, "w1", 1, S.FAILED)) is None
    )  # now terminal


def test_a_pending_cancel_wins_over_any_other_outcome(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    for target in (S.SUCCEEDED, S.FAILED, S.TIMED_OUT):
        task = new_task(session_factory, alice)
        claimed = tx(session_factory, lambda r: r.claim("w1", 10, LEASE))
        mine = next(t for t in claimed if t.id == task.id)
        tx(session_factory, lambda r: r.request_cancel(task.id, alice.user_id))
        outcome = tx(
            session_factory,
            lambda r: r.finish(
                task.id,
                "w1",
                mine.attempt,
                target,
                result={"x": 1},
                error_code=TaskErrorCode.INTERNAL_ERROR,
            ),
        )
        done = fetch(session_factory, task.id)
        assert outcome is S.CANCELLED and (done.status, done.result, done.error_code) == (
            "cancelled",
            None,
            None,
        )


def test_failed_and_timed_out_record_codes_and_failure_audit(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    first, second = new_task(session_factory, alice), new_task(session_factory, alice)
    tx(session_factory, lambda r: r.claim("w1", 2, LEASE))
    tx(
        session_factory,
        lambda r: r.finish(first.id, "w1", 1, S.FAILED, error_code=TaskErrorCode.PROVIDER_ERROR),
    )
    tx(
        session_factory,
        lambda r: r.finish(second.id, "w1", 1, S.TIMED_OUT, error_code=TaskErrorCode.TIMED_OUT),
    )
    assert fetch(session_factory, first.id).error_code == "provider_error"
    assert fetch(session_factory, second.id).error_code == "timed_out"
    assert [a.outcome for a in audit_rows(session_factory, "task.finish")] == ["failure", "failure"]


def test_illegal_transitions_raise(alice: Seeded, session_factory: SessionFactory) -> None:
    task = new_task(session_factory, alice)
    claim_one(session_factory)
    with pytest.raises(InvalidTransitionError):
        tx(session_factory, lambda r: r.finish(task.id, "w1", 1, S.QUEUED))
    with pytest.raises(InvalidTransitionError):
        tx(session_factory, lambda r: r.requeue_by_operator(task.id))  # running is not requeueable


# ----- retry and release -----


def test_schedule_retry_requeues_with_delay_and_keeps_the_attempt(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    task = new_task(session_factory, alice)
    claim_one(session_factory)
    outcome = tx(
        session_factory,
        lambda r: r.schedule_retry(
            task.id, "w1", 1, code=TaskErrorCode.PROVIDER_UNAVAILABLE, delay_seconds=30
        ),
    )
    assert outcome is S.QUEUED
    queued = fetch(session_factory, task.id)
    assert (queued.status, queued.attempt, queued.error_code, queued.lease_owner) == (
        "queued",
        1,
        "provider_unavailable",
        None,
    )
    assert tx(session_factory, lambda r: r.claim("w1", 1, LEASE)) == []  # not due yet
    make_due(test_db_url, task.id)
    [reclaimed] = tx(session_factory, lambda r: r.claim("w1", 1, LEASE))
    assert reclaimed.attempt == 2
    retry_event = [
        e
        for e in tx(session_factory, lambda r: r.events_after(task.id, 0, 20))
        if e.event_type == "retry_scheduled"
    ]
    assert retry_event[0].payload == {
        "attempt": 1,
        "delay_seconds": 30.0,
        "code": "provider_unavailable",
    }


def test_release_does_not_consume_the_attempt(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    task = new_task(session_factory, alice)
    claim_one(session_factory)
    assert tx(session_factory, lambda r: r.release(task.id, "w1", 1)) is S.QUEUED
    released = fetch(session_factory, task.id)
    assert (released.status, released.attempt) == ("queued", 0)
    [again] = tx(session_factory, lambda r: r.claim("w2", 1, LEASE))
    assert again.attempt == 1


# ----- cancellation -----


def test_cancel_outcomes_by_state(
    alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    queued = new_task(session_factory, alice)
    result = tx(session_factory, lambda r: r.request_cancel(queued.id, alice.user_id))
    assert (result.outcome, result.task.status) == ("cancelled", "cancelled")
    assert tx(session_factory, lambda r: r.claim("w1", 5, LEASE)) == []  # never claimable again
    assert (
        tx(session_factory, lambda r: r.request_cancel(queued.id, alice.user_id)).outcome == "noop"
    )

    running = new_task(session_factory, alice)
    claim_one(session_factory)
    result = tx(session_factory, lambda r: r.request_cancel(running.id, alice.user_id))
    assert (result.outcome, result.task.status) == ("cancel_requested", "cancel_requested")
    assert (
        tx(session_factory, lambda r: r.request_cancel(running.id, alice.user_id)).outcome == "noop"
    )

    done = new_task(session_factory, alice)
    claim_one(session_factory)
    tx(session_factory, lambda r: r.finish(done.id, "w1", 1, S.SUCCEEDED))
    with pytest.raises(TaskNotCancellableError):
        tx(session_factory, lambda r: r.request_cancel(done.id, alice.user_id))
    with pytest.raises(NotFoundError):
        tx(session_factory, lambda r: r.request_cancel(running.id, bob.user_id))
    with pytest.raises(NotFoundError):
        tx(session_factory, lambda r: r.request_cancel(uuid.uuid4(), alice.user_id))


# ----- reaper -----


def test_reaper_handles_each_expired_state_and_ignores_healthy_leases(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    healthy, expired, exhausted, cancelling = (
        new_task(session_factory, alice),
        new_task(session_factory, alice),
        new_task(session_factory, alice, max_attempts=1),
        new_task(session_factory, alice),
    )
    tx(session_factory, lambda r: r.claim("w1", 10, LEASE))
    tx(session_factory, lambda r: r.request_cancel(cancelling.id, alice.user_id))
    for task in (expired, exhausted, cancelling):
        expire_lease(test_db_url, task.id)

    outcomes = {r.task_id: r.outcome for r in _reap(session_factory)}
    assert outcomes == {expired.id: "requeued", exhausted.id: "failed", cancelling.id: "cancelled"}
    assert status_of(session_factory, healthy.id) is S.RUNNING
    assert fetch(session_factory, exhausted.id).error_code == "worker_lost"
    finish_audits = audit_rows(session_factory, "task.finish")
    assert sorted(a.outcome for a in finish_audits) == ["failure", "success"]
    assert _reap(session_factory) == []  # idempotent


def test_concurrent_reapers_do_not_double_process(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    tasks = [new_task(session_factory, alice) for _ in range(6)]
    tx(session_factory, lambda r: r.claim("w1", 10, LEASE))
    for task in tasks:
        expire_lease(test_db_url, task.id)
    batches = concurrently(
        session_factory, 4, lambda repo, i: repo.reap_expired(10, retry_base=5, retry_cap=120)
    )
    reaped = [r.task_id for batch in batches for r in batch]
    assert sorted(reaped) == sorted(t.id for t in tasks)
    for task in tasks:
        events = tx(session_factory, lambda r: r.events_after(task.id, 0, 20))
        assert [e.event_type for e in events].count("lease_expired_requeued") == 1


# ----- events -----


def test_event_sequences_are_unique_and_gapless_under_concurrency(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    from friday.domain.task_state import TaskEventType

    task = new_task(session_factory, alice)
    concurrently(
        session_factory,
        20,
        lambda repo, i: repo.add_event(task.id, TaskEventType.PROGRESS, {"i": i}),
    )
    events = tx(session_factory, lambda r: r.events_after(task.id, 0, 100))
    assert [e.sequence for e in events] == list(range(1, 22))  # created + 20


def test_events_paginate_and_payloads_are_redacted_and_bounded(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    from friday.domain.task_state import TaskEventType

    task = new_task(session_factory, alice)
    secret = "fri_abcdefghij1234567890"
    tx(
        session_factory,
        lambda r: r.add_event(task.id, TaskEventType.PROGRESS, {"note": f"token {secret}"}),
    )
    tx(
        session_factory,
        lambda r: r.add_event(task.id, TaskEventType.PROGRESS, {"big": "word " * 1000}),
    )
    events = tx(session_factory, lambda r: r.events_after(task.id, 0, 10))
    assert secret not in repr([e.payload for e in events])
    assert events[2].payload == {"_truncated": True}
    page = tx(session_factory, lambda r: r.events_after(task.id, 1, 1))
    assert [e.sequence for e in page] == [2]


# ----- operator tools and retention -----


def test_operator_requeue_only_for_failed_or_timed_out(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    failed, ok = new_task(session_factory, alice), new_task(session_factory, alice)
    tx(session_factory, lambda r: r.claim("w1", 2, LEASE))
    tx(
        session_factory,
        lambda r: r.finish(failed.id, "w1", 1, S.FAILED, error_code=TaskErrorCode.PROVIDER_ERROR),
    )
    tx(session_factory, lambda r: r.finish(ok.id, "w1", 1, S.SUCCEEDED, result={}))
    revived = tx(session_factory, lambda r: r.requeue_by_operator(failed.id))
    assert (revived.status, revived.attempt, revived.error_code, revived.finished_at) == (
        "queued",
        0,
        None,
        None,
    )
    assert len(audit_rows(session_factory, "task.requeue")) == 1
    with pytest.raises(InvalidTransitionError):
        tx(session_factory, lambda r: r.requeue_by_operator(ok.id))


def test_purge_removes_only_old_terminal_tasks_and_their_events(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    old_done, recent_done, old_queued = (new_task(session_factory, alice) for _ in range(3))
    tx(session_factory, lambda r: r.claim("w1", 2, LEASE))
    tx(session_factory, lambda r: r.finish(old_done.id, "w1", 1, S.SUCCEEDED, result={}))
    tx(session_factory, lambda r: r.finish(recent_done.id, "w1", 1, S.SUCCEEDED, result={}))
    sql(
        test_db_url,
        "UPDATE tasks SET finished_at = now() - interval '40 days' WHERE id = %s",
        (old_done.id,),
    )
    sql(
        test_db_url,
        "UPDATE tasks SET created_at = now() - interval '90 days' WHERE id = %s",
        (old_queued.id,),
    )
    assert tx(session_factory, lambda r: r.purge_terminal_older_than(30)) == 1
    remaining = {row[0] for row in sql(test_db_url, "SELECT id FROM tasks")}
    assert remaining == {recent_done.id, old_queued.id}
    assert (
        sql(test_db_url, "SELECT count(*) FROM task_events WHERE task_id = %s", (old_done.id,))[0][
            0
        ]
        == 0
    )


def test_purge_never_touches_active_tasks_even_with_a_stale_finished_at(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    active = new_task(session_factory, alice)  # corrupt row: queued yet carrying an old finished_at
    sql(
        test_db_url,
        "UPDATE tasks SET finished_at = now() - interval '90 days' WHERE id = %s",
        (active.id,),
    )
    assert tx(session_factory, lambda r: r.purge_terminal_older_than(30)) == 0
    assert status_of(session_factory, active.id) is S.QUEUED
