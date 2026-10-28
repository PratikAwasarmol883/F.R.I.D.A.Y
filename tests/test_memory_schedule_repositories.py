import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import Memory, Task
from friday.persistence.repositories.memories import MemoryRepository
from friday.persistence.repositories.schedules import ScheduleRepository

from .conftest import Seeded, SessionFactory, run_db
from .schedule_helpers import NOW, new_job, scheduled_task
from .task_helpers import concurrently, sql, tx


def new_memory(
    factory: SessionFactory,
    who: Seeded,
    value: str = "likes tea",
    *,
    kind: str = "preference",
    status: str = "approved",
    source: str = "user",
) -> Memory:
    async def go(session: AsyncSession) -> Memory:
        memory = await MemoryRepository(session).create(
            user_id=who.user_id, kind=kind, value=value, status=status, source=source
        )
        await session.commit()
        return memory

    return run_db(factory, go)


def test_memories_are_owner_scoped_filterable_and_really_deleted(
    alice: Seeded, bob: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    approved = new_memory(session_factory, alice, "A")
    new_memory(session_factory, alice, "P", status="pending", source="assistant")

    async def view(session: AsyncSession, who: Seeded) -> tuple[Any, ...]:
        repo = MemoryRepository(session)
        return (
            await repo.get_owned(approved.id, who.user_id),
            [m.value for m in await repo.list_for_user(who.user_id, "approved", 10)],
            sorted(m.value for m in await repo.list_for_user(who.user_id, None, 10)),
            await repo.count(who.user_id),
            await repo.count(who.user_id, "pending"),
        )

    found, approved_values, all_values, total, pending = run_db(
        session_factory, lambda s: view(s, alice)
    )
    assert found is not None and approved_values == ["A"] and all_values == ["A", "P"]
    assert (total, pending) == (2, 1)
    other = run_db(session_factory, lambda s: view(s, bob))
    assert other[0] is None and other[2] == [] and other[3] == 0

    async def delete(session: AsyncSession, who: Seeded) -> bool:
        deleted = await MemoryRepository(session).delete_owned(approved.id, who.user_id)
        await session.commit()
        return deleted

    assert run_db(session_factory, lambda s: delete(s, bob)) is False
    assert run_db(session_factory, lambda s: delete(s, alice)) is True
    assert sql(test_db_url, "SELECT count(*) FROM memories WHERE value = 'A'")[0][0] == 0


@pytest.mark.parametrize("fields", [{"kind": "secret"}, {"status": "maybe"}, {"source": "model"}])
def test_memory_constraints_reject_bad_values(
    alice: Seeded, session_factory: SessionFactory, fields: dict[str, str]
) -> None:
    with pytest.raises(IntegrityError):
        new_memory(session_factory, alice, **fields)


def test_due_schedules_are_row_locked_so_concurrent_dispatchers_never_share_one(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    jobs = [new_job(session_factory, alice, f"j{i}", NOW - timedelta(minutes=i)) for i in range(6)]
    new_job(session_factory, alice, "future", NOW + timedelta(hours=1))
    new_job(session_factory, alice, "off", NOW - timedelta(hours=1), enabled=False)

    async def dispatcher() -> list[Any]:
        async with session_factory() as session:
            rows = await ScheduleRepository(session).lock_due(NOW, 2)
            await asyncio.sleep(0.2)  # hold the row locks while the others try
            ids = [row.id for row in rows]
            await session.commit()
            return ids

    async def main() -> list[list[Any]]:
        return list(await asyncio.gather(*[dispatcher() for _ in range(5)]))

    claimed = [job_id for batch in asyncio.run(main()) for job_id in batch]
    assert len(claimed) == len(set(claimed)) == 6 and set(claimed) == {j.id for j in jobs}


def test_one_task_per_occurrence_is_enforced_by_the_database(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice)
    results = concurrently(
        session_factory, 8, lambda repo, _: scheduled_task(repo, alice, job, NOW)
    )
    assert sum(1 for r in results if r is not None) == 1  # one winner; the rest see a conflict
    assert (
        sql(test_db_url, "SELECT count(*) FROM tasks WHERE schedule_id = %s", (job.id,))[0][0] == 1
    )
    assert tx(session_factory, lambda r: scheduled_task(r, alice, job, NOW)) is None
    tomorrow = tx(session_factory, lambda r: scheduled_task(r, alice, job, NOW + timedelta(days=1)))
    assert isinstance(tomorrow, Task)  # the next occurrence is a different task


def test_deleting_a_schedule_keeps_its_past_runs_as_plain_tasks(
    alice: Seeded, session_factory: SessionFactory, test_db_url: str
) -> None:
    job = new_job(session_factory, alice)
    task = tx(session_factory, lambda r: scheduled_task(r, alice, job, NOW))
    assert task is not None

    async def delete(session: AsyncSession) -> bool:
        deleted = await ScheduleRepository(session).delete_owned(job.id, alice.user_id)
        await session.commit()
        return deleted

    assert run_db(session_factory, delete) is True
    assert sql(test_db_url, "SELECT schedule_id FROM tasks WHERE id = %s", (task.id,)) == [(None,)]
