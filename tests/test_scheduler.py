import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from friday.core.config import Settings
from friday.services.auth_service import AuthService
from friday.workers.scheduler import DispatchSummary, Scheduler

from .conftest import Seeded, SessionFactory, run_db
from .helpers import audit_rows
from .schedule_helpers import new_job, scheduled_task
from .task_helpers import new_task, sql, tx
from .worker_handlers import ScriptedHandler, registry_for, succeed
from .worker_helpers import TERMINAL, drive, settings_for, state, wait_until

OCC = datetime(2026, 10, 29, 7, 30, tzinfo=UTC)  # a 07:30 UTC occurrence


def make(factory: SessionFactory, now: datetime, **settings: Any) -> Scheduler:
    config = Settings(_env_file=None).model_copy(update=settings)
    return Scheduler(factory, config, clock=lambda: now)


def dispatch(factory: SessionFactory, now: datetime, **settings: Any) -> DispatchSummary:
    return asyncio.run(make(factory, now, **settings).dispatch_due())


def job_row(db_url: str, job_id: Any) -> tuple[Any, ...]:
    return sql(
        db_url,
        "SELECT enabled, next_run_at, last_run_at, last_task_id FROM scheduled_jobs WHERE id = %s",
        (job_id,),
    )[0]


def runs(db_url: str, job_id: Any) -> list[tuple[Any, ...]]:
    return sql(
        db_url,
        "SELECT scheduled_for, status FROM tasks WHERE schedule_id = %s ORDER BY scheduled_for",
        (job_id,),
    )


# one run per occurrence


def test_a_due_job_creates_exactly_one_task_and_advances_to_the_next_occurrence(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC)
    now = OCC + timedelta(seconds=5)
    assert dispatch(session_factory, now) == DispatchSummary(dispatched=1)
    enabled, next_run, last_run, last_task = job_row(test_db_url, job.id)
    assert (enabled, next_run, last_run) == (
        True,
        OCC + timedelta(days=1),
        now,
    ) and last_task is not None
    assert runs(test_db_url, job.id) == [(OCC, "queued")]
    [audit] = audit_rows(session_factory, "schedule.run")
    assert audit.target_id == str(last_task) and audit.outcome == "success"
    events = tx(session_factory, lambda r: r.events_after(last_task, 0, 10))
    assert [e.event_type for e in events] == ["created"] and events[0].payload["scheduled"] is True
    assert (
        dispatch(session_factory, now) == DispatchSummary()
    )  # nothing left to do for this occurrence
    assert len(runs(test_db_url, job.id)) == 1


def test_not_yet_due_or_disabled_jobs_are_left_alone(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    future = new_job(session_factory, alice, "future", OCC + timedelta(hours=1))
    off = new_job(session_factory, alice, "off", OCC, enabled=False)
    assert dispatch(session_factory, OCC + timedelta(seconds=5)) == DispatchSummary()
    assert runs(test_db_url, future.id) == [] and runs(test_db_url, off.id) == []


def test_many_concurrent_dispatchers_create_one_task_per_occurrence(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC)
    now = OCC + timedelta(seconds=5)

    async def main() -> list[DispatchSummary]:
        return list(
            await asyncio.gather(*[make(session_factory, now).dispatch_due() for _ in range(10)])
        )

    summaries = asyncio.run(main())
    assert sum(s.dispatched for s in summaries) == 1
    assert len(runs(test_db_url, job.id)) == 1
    assert job_row(test_db_url, job.id)[1] == OCC + timedelta(days=1)


def test_a_restarted_scheduler_does_not_repeat_an_occurrence(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC)
    dispatch(session_factory, OCC + timedelta(seconds=1))
    for later in (
        OCC + timedelta(seconds=2),
        OCC + timedelta(minutes=30),
        OCC + timedelta(hours=23),
    ):
        assert (
            dispatch(session_factory, later) == DispatchSummary()
        )  # a brand-new instance each time
    assert len(runs(test_db_url, job.id)) == 1
    day_two = dispatch(session_factory, OCC + timedelta(days=1, seconds=1))
    assert day_two.dispatched == 1 and len(runs(test_db_url, job.id)) == 2


def test_the_database_uniqueness_guard_backstops_a_duplicate(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC)
    tx(
        session_factory, lambda r: scheduled_task(r, alice, job, OCC)
    )  # as if another dispatcher raced us
    assert dispatch(session_factory, OCC + timedelta(seconds=1)) == DispatchSummary(skipped=1)
    assert len(runs(test_db_url, job.id)) == 1 and job_row(test_db_url, job.id)[
        1
    ] == OCC + timedelta(days=1)


# lateness: grace and no backfill


def test_late_within_grace_runs_and_beyond_grace_is_skipped_and_audited(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    near = new_job(session_factory, alice, "near", OCC)
    assert dispatch(session_factory, OCC + timedelta(minutes=50)).dispatched == 1
    assert len(runs(test_db_url, near.id)) == 1

    far = new_job(session_factory, alice, "far", OCC)
    sql(test_db_url, "DELETE FROM tasks")  # keep the active-task limit out of the picture
    summary = dispatch(session_factory, OCC + timedelta(hours=2))
    assert summary == DispatchSummary(missed=1)  # `near` is not due again; only `far` was late
    assert runs(test_db_url, far.id) == []
    assert [a.outcome for a in audit_rows(session_factory, "schedule.missed")] == ["failure"]
    assert job_row(test_db_url, far.id)[1] == OCC + timedelta(days=1)


def test_long_downtime_never_backfills_and_runs_only_the_latest_occurrence_still_in_grace(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC - timedelta(days=3))
    now = OCC + timedelta(
        days=1, minutes=10
    )  # three missed days, but today's 07:30 is only 10 min ago
    summary = dispatch(session_factory, now)
    assert summary.dispatched == 1 and summary.missed == 0
    assert runs(test_db_url, job.id) == [
        (OCC + timedelta(days=1), "queued")
    ]  # one run, the latest due
    assert job_row(test_db_url, job.id)[1] == OCC + timedelta(days=2)
    assert (
        len(audit_rows(session_factory, "schedule.missed")) == 1
    )  # the skipped days, recorded once

    stale = new_job(session_factory, alice, "stale", OCC - timedelta(days=3))
    sql(test_db_url, "DELETE FROM tasks")
    summary = dispatch(session_factory, OCC + timedelta(days=1, hours=5))
    assert (
        runs(test_db_url, stale.id) == [] and summary.missed >= 1
    )  # nothing in grace: no run at all
    assert job_row(test_db_url, stale.id)[1] > OCC + timedelta(days=1, hours=5)  # fast-forwarded


def test_a_schedule_untouched_for_years_is_fast_forwarded_not_walked(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=datetime(2020, 1, 1, 7, 30, tzinfo=UTC))
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    assert dispatch(session_factory, now).dispatched == 0
    assert runs(test_db_url, job.id) == [] and job_row(test_db_url, job.id)[1] > now


# authorization and limits


def test_a_revoked_creating_device_disables_the_schedule(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC)

    async def revoke(session: Any) -> None:
        await AuthService(session).revoke_device(alice.device_id)

    run_db(session_factory, revoke)
    assert dispatch(session_factory, OCC + timedelta(seconds=1)) == DispatchSummary(disabled=1)
    assert job_row(test_db_url, job.id)[0] is False and runs(test_db_url, job.id) == []
    assert [a.outcome for a in audit_rows(session_factory, "schedule.disabled")] == ["denied"]
    assert (
        dispatch(session_factory, OCC + timedelta(days=2)) == DispatchSummary()
    )  # and it stays off


def test_the_active_task_limit_skips_the_occurrence(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=OCC)
    new_task(session_factory, alice)  # already one active task
    summary = dispatch(session_factory, OCC + timedelta(seconds=1), max_active_tasks_per_user=1)
    assert summary == DispatchSummary(skipped=1) and runs(test_db_url, job.id) == []
    assert [a.outcome for a in audit_rows(session_factory, "schedule.skipped")] == ["denied"]
    assert job_row(test_db_url, job.id)[1] == OCC + timedelta(days=1)


def test_each_call_handles_at_most_one_batch(
    alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    for i in range(5):
        new_job(session_factory, alice if i % 2 else bob, f"j{i}", OCC - timedelta(minutes=i))
    first = dispatch(session_factory, OCC, scheduler_batch=2)
    assert first.dispatched == 2
    assert dispatch(session_factory, OCC, scheduler_batch=10).dispatched == 3


# time zones


def test_dst_gap_day_runs_once_at_the_shifted_time_and_the_next_day_resumes(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    spring = {"kind": "daily", "time": "02:30", "days": []}
    gap_run = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 02:30 does not exist: 03:30 EDT
    job = new_job(session_factory, alice, at=gap_run, schedule=spring, timezone="America/New_York")
    assert dispatch(session_factory, gap_run + timedelta(minutes=1)).dispatched == 1
    assert job_row(test_db_url, job.id)[1] == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)  # 02:30 EDT
    assert (
        dispatch(session_factory, gap_run + timedelta(hours=10)).dispatched == 0
    )  # same day: nothing more
    sql(test_db_url, "DELETE FROM tasks")
    assert dispatch(session_factory, datetime(2026, 3, 9, 6, 31, tzinfo=UTC)).dispatched == 1
    assert [r[0] for r in runs(test_db_url, job.id)] == [datetime(2026, 3, 9, 6, 30, tzinfo=UTC)]


def test_fall_back_day_runs_once(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    fall = {"kind": "daily", "time": "01:30", "days": []}
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # the first 01:30 (EDT)
    job = new_job(session_factory, alice, at=first, schedule=fall, timezone="America/New_York")
    assert dispatch(session_factory, first + timedelta(minutes=1)).dispatched == 1
    second_pass = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 again, now EST
    assert dispatch(session_factory, second_pass + timedelta(minutes=1)).dispatched == 0
    assert len(runs(test_db_url, job.id)) == 1


def test_weekly_schedules_walk_through_their_days(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    weekly = {"kind": "weekly", "time": "09:00", "days": ["mon", "wed"]}
    monday = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    job = new_job(session_factory, alice, at=monday, schedule=weekly)
    seen = []
    for _ in range(4):
        _, next_run, *_ = job_row(test_db_url, job.id)
        assert dispatch(session_factory, next_run + timedelta(seconds=1)).dispatched == 1
        seen.append(next_run.strftime("%a"))
        sql(test_db_url, "DELETE FROM tasks")
    assert seen == ["Mon", "Wed", "Mon", "Wed"]


# end to end with a real worker loop


def test_a_real_worker_dispatches_and_runs_a_due_schedule(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str, make_client: Any
) -> None:
    job = new_job(session_factory, alice, at=datetime.now(UTC) - timedelta(minutes=1))
    handler = ScriptedHandler(lambda ctx: succeed(ctx, session_factory))
    cfg = settings_for()
    scheduler = Scheduler(session_factory, cfg)

    async def scenario(_: list[Any]) -> None:
        await wait_until(lambda: len(runs(test_db_url, job.id)) == 1)
        await wait_until(lambda: runs(test_db_url, job.id)[0][1] in TERMINAL)

    drive(session_factory, registry_for(handler), scenario, settings=cfg, scheduler=scheduler)
    [(scheduled_for, status)] = runs(test_db_url, job.id)
    assert status == "succeeded" and len(handler.runs) == 1
    assert job_row(test_db_url, job.id)[1] > datetime.now(UTC)  # scheduled for the future
    assert state(
        test_db_url,
        sql(test_db_url, "SELECT id FROM tasks WHERE schedule_id = %s", (job.id,))[0][0],
    )[:2] == ("succeeded", 1)
    assert scheduled_for is not None


def test_a_worker_never_runs_the_schedule_of_a_revoked_device(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=datetime.now(UTC) - timedelta(minutes=1))

    async def revoke(session: Any) -> None:
        await AuthService(session).revoke_device(alice.device_id)

    run_db(session_factory, revoke)
    handler = ScriptedHandler(lambda ctx: succeed(ctx, session_factory))
    cfg = settings_for()

    async def scenario(_: list[Any]) -> None:
        await wait_until(lambda: job_row(test_db_url, job.id)[0] is False)

    drive(
        session_factory,
        registry_for(handler),
        scenario,
        settings=cfg,
        scheduler=Scheduler(session_factory, cfg),
    )
    assert runs(test_db_url, job.id) == [] and handler.runs == []


def test_worker_without_a_scheduler_dispatches_nothing(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice, at=datetime.now(UTC) - timedelta(minutes=1))

    async def scenario(workers: list[Any]) -> None:
        assert await workers[0].dispatch_schedules() == 0
        await asyncio.sleep(0.2)

    drive(
        session_factory,
        registry_for(ScriptedHandler(lambda ctx: succeed(ctx, session_factory))),
        scenario,
    )
    assert runs(test_db_url, job.id) == []
