import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

import psycopg
from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.task_state import TaskStatus
from friday.persistence.models import Task
from friday.persistence.repositories.tasks import TaskRepository

from .conftest import Seeded, SessionFactory, run_db

T = TypeVar("T")
LEASE = timedelta(seconds=60)


def tx(factory: SessionFactory, fn: Callable[[TaskRepository], Awaitable[T]]) -> T:
    """Run one repository operation in its own committed transaction."""

    async def go(session: AsyncSession) -> T:
        result = await fn(TaskRepository(session))
        await session.commit()
        return result

    return run_db(factory, go)


def concurrently(
    factory: SessionFactory, count: int, fn: Callable[[TaskRepository, int], Awaitable[T]]
) -> list[T]:
    async def one(index: int) -> T:
        async with factory() as session:
            result = await fn(TaskRepository(session), index)
            await session.commit()
            return result

    async def main() -> list[T]:
        return list(await asyncio.gather(*[one(i) for i in range(count)]))

    return asyncio.run(main())


def new_task(
    factory: SessionFactory,
    who: Seeded,
    *,
    key: str | None = None,
    message: str = "hello",
    max_attempts: int = 3,
    task_input: dict[str, Any] | None = None,
) -> Task:
    async def create(repo: TaskRepository) -> Task:
        task, _ = await repo.create(
            user_id=who.user_id,
            device_id=who.device_id,
            task_type="assistant.ask",
            task_input=task_input or {"message": message, "conversation_id": None},
            idempotency_key=key,
            fingerprint=uuid.uuid4().hex * 2,
            max_attempts=max_attempts,
            max_runtime_seconds=300,
        )
        return task

    return tx(factory, create)


def fetch(factory: SessionFactory, task_id: uuid.UUID) -> Task:
    async def get(repo: TaskRepository) -> Task:
        task = await repo.get(task_id)
        assert task is not None
        return task

    return tx(factory, get)


def sql(db_url: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(db_url) as conn:
        cursor = conn.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def expire_lease(db_url: str, task_id: uuid.UUID) -> None:
    sql(
        db_url,
        "UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
        (task_id,),
    )


def make_due(db_url: str, task_id: uuid.UUID) -> None:
    sql(
        db_url, "UPDATE tasks SET run_after = now() - interval '1 second' WHERE id = %s", (task_id,)
    )


def claim_one(factory: SessionFactory, worker: str = "w1") -> Task:
    claimed = tx(factory, lambda repo: repo.claim(worker, 1, LEASE))
    assert len(claimed) == 1, claimed
    return claimed[0]


def status_of(factory: SessionFactory, task_id: uuid.UUID) -> TaskStatus:
    return TaskStatus(fetch(factory, task_id).status)


async def atx(factory: SessionFactory, fn: Callable[[TaskRepository], Awaitable[T]]) -> T:
    """Async variant of `tx` for code that already runs inside an event loop."""
    async with factory() as session:
        result = await fn(TaskRepository(session))
        await session.commit()
        return result
