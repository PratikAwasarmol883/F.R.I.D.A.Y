"""Builders for scheduling tests."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import ScheduledJob
from friday.persistence.repositories.schedules import ScheduleRepository
from friday.persistence.repositories.tasks import TaskRepository

from .conftest import Seeded, SessionFactory, run_db

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
DAILY_0730 = {"kind": "daily", "time": "07:30", "days": []}


def new_job(
    factory: SessionFactory,
    who: Seeded,
    name: str = "daily",
    at: datetime = NOW,
    enabled: bool = True,
    *,
    schedule: dict[str, Any] | None = None,
    timezone: str = "UTC",
    task_type: str = "assistant.ask",
    task_input: dict[str, Any] | None = None,
) -> ScheduledJob:
    async def go(session: AsyncSession) -> ScheduledJob:
        job = await ScheduleRepository(session).create(
            user_id=who.user_id,
            device_id=who.device_id,
            name=name,
            task_type=task_type,
            task_input=task_input or {"message": "brief me", "conversation_id": None},
            schedule=schedule or DAILY_0730,
            timezone=timezone,
            enabled=enabled,
            next_run_at=at,
        )
        await session.commit()
        return job

    return run_db(factory, go)


def scheduled_task(repo: TaskRepository, who: Seeded, job: ScheduledJob, when: datetime) -> Any:
    return repo.create_scheduled(
        user_id=who.user_id,
        device_id=who.device_id,
        task_type="assistant.ask",
        task_input={"message": "x"},
        fingerprint="f" * 64,
        max_attempts=3,
        max_runtime_seconds=300,
        schedule_id=job.id,
        scheduled_for=when,
    )
