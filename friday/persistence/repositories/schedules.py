import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import ScheduledJob, Task


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        name: str,
        task_type: str,
        task_input: dict[str, Any],
        schedule: dict[str, Any],
        timezone: str,
        enabled: bool,
        next_run_at: datetime,
    ) -> ScheduledJob:
        job = ScheduledJob(
            id=uuid.uuid4(),
            user_id=user_id,
            device_id=device_id,
            name=name,
            task_type=task_type,
            task_input=task_input,
            schedule=schedule,
            timezone=timezone,
            enabled=enabled,
            next_run_at=next_run_at,
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def get_owned(
        self, schedule_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> ScheduledJob | None:
        stmt = (
            select(ScheduledJob)
            .where(ScheduledJob.id == schedule_id, ScheduledJob.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def list_for_user(self, user_id: uuid.UUID) -> list[ScheduledJob]:
        stmt = (
            select(ScheduledJob)
            .where(ScheduledJob.user_id == user_id)
            .order_by(ScheduledJob.created_at, ScheduledJob.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(ScheduledJob).where(ScheduledJob.user_id == user_id)
        return int(await self._session.scalar(stmt) or 0)

    async def delete_owned(self, schedule_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(ScheduledJob).where(
                ScheduledJob.id == schedule_id, ScheduledJob.user_id == user_id
            )
        )
        return bool(getattr(result, "rowcount", 0))

    async def due_ids(self, now: datetime, limit: int) -> list[uuid.UUID]:
        stmt = (
            select(ScheduledJob.id)
            .where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)
            .order_by(ScheduledJob.next_run_at)
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    async def lock_one_due(self, schedule_id: uuid.UUID, now: datetime) -> ScheduledJob | None:
        """Re-check dueness under a row lock; None if someone else already took or advanced it."""
        stmt = (
            select(ScheduledJob)
            .where(
                ScheduledJob.id == schedule_id,
                ScheduledJob.enabled.is_(True),
                ScheduledJob.next_run_at <= now,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def lock_due(self, now: datetime, limit: int) -> list[ScheduledJob]:
        """Due jobs, row-locked until commit; other dispatchers skip rows we hold."""
        stmt = (
            select(ScheduledJob)
            .where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)
            .order_by(ScheduledJob.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def runs(self, schedule_id: uuid.UUID, user_id: uuid.UUID, limit: int) -> list[Task]:
        stmt = (
            select(Task)
            .where(Task.schedule_id == schedule_id, Task.user_id == user_id)
            .order_by(Task.scheduled_for.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())
