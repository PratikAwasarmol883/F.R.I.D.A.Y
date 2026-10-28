"""What a task handler may do while it runs. Every write is fenced by (worker, attempt)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.domain.task_state import LEASED_STATUSES, TaskEventType, TaskStatus
from friday.persistence.models import Task
from friday.persistence.repositories.tasks import TaskRepository
from friday.workers.errors import LeaseLostError, TaskCancelled


class TaskContext:
    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        device_id: uuid.UUID | None,
        attempt: int,
        worker_id: str,
        task_input: dict[str, Any],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.task_id = task_id
        self.user_id = user_id
        self.device_id = device_id
        self.attempt = attempt
        self.worker_id = worker_id
        self.task_input = task_input
        self._session_factory = session_factory

    async def emit(self, step: str, payload: dict[str, Any] | None = None) -> None:
        """Progress event in its own short transaction; payloads must not contain user text."""
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            if await repo.lock_fenced(self.task_id, self.worker_id, self.attempt) is None:
                raise LeaseLostError
            await repo.add_event(
                self.task_id, TaskEventType.PROGRESS, {**(payload or {}), "step": step}
            )
            await session.commit()

    async def check_cancelled(self) -> None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(Task.status, Task.lease_owner, Task.attempt).where(
                        Task.id == self.task_id
                    )
                )
            ).one_or_none()
        if (
            row is None
            or row.lease_owner != self.worker_id
            or row.attempt != self.attempt
            or TaskStatus(row.status) not in LEASED_STATUSES
        ):
            raise LeaseLostError
        if TaskStatus(row.status) is TaskStatus.CANCEL_REQUESTED:
            raise TaskCancelled

    async def lock_for_finish(self, session: AsyncSession) -> None:
        """Take the task row lock inside the caller's transaction before any write."""
        task = await TaskRepository(session).lock_fenced(self.task_id, self.worker_id, self.attempt)
        if task is None:
            raise LeaseLostError
        if TaskStatus(task.status) is TaskStatus.CANCEL_REQUESTED:
            raise TaskCancelled

    async def mark_succeeded(self, session: AsyncSession, result: dict[str, Any]) -> None:
        """Mark success in the caller's transaction, atomically with the handler's own writes."""
        outcome = await TaskRepository(session).finish(
            self.task_id, self.worker_id, self.attempt, TaskStatus.SUCCEEDED, result=result
        )
        if outcome is None:
            raise LeaseLostError
        if outcome is not TaskStatus.SUCCEEDED:
            raise TaskCancelled
