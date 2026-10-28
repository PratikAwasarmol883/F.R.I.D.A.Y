"""Turns due schedules into ordinary durable tasks, at most once per occurrence."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.core.config import Settings
from friday.core.logging_config import get_logger
from friday.domain.enums import AuditOutcome
from friday.domain.schedule import (
    ScheduleSpec,
    latest_due_occurrence,
    next_occurrence,
    parse_timezone,
)
from friday.domain.task_state import TaskType
from friday.persistence.models import ScheduledJob
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.devices import DeviceRepository
from friday.persistence.repositories.schedules import ScheduleRepository
from friday.persistence.repositories.tasks import TaskRepository
from friday.services.task_service import fingerprint_for

logger = get_logger(__name__)


@dataclass
class DispatchSummary:
    dispatched: int = 0
    skipped: int = 0
    missed: int = 0
    disabled: int = 0


class Scheduler:
    """Safe to run in many workers at once: each job is handled under a row lock, advanced in the
    same transaction, and backed by a database uniqueness guarantee on (schedule, occurrence)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock

    async def dispatch_due(self) -> DispatchSummary:
        summary = DispatchSummary()
        now = self._clock()
        async with self._session_factory() as session:
            ids = await ScheduleRepository(session).due_ids(now, self._settings.scheduler_batch)
        for schedule_id in ids:
            # One short transaction per job: locks are never held across jobs, so dispatchers
            # that share users cannot deadlock.
            async with self._session_factory() as session:
                job = await ScheduleRepository(session).lock_one_due(schedule_id, now)
                if job is None:
                    continue  # another dispatcher already handled this occurrence
                outcome = await self._dispatch(session, job, now)
                await session.commit()
            setattr(summary, outcome, getattr(summary, outcome) + 1)
        return summary

    async def _dispatch(self, session: AsyncSession, job: ScheduledJob, now: datetime) -> str:
        audit = AuditRepository(session)
        tasks = TaskRepository(session)
        spec = ScheduleSpec.model_validate(job.schedule)
        zone = parse_timezone(job.timezone)
        occurrence, skipped_earlier = latest_due_occurrence(spec, zone, job.next_run_at, now)
        job.next_run_at = next_occurrence(spec, zone, max(now, occurrence))  # always advance first

        async def note(
            action: str, outcome: AuditOutcome = AuditOutcome.SUCCESS, target: str | None = None
        ) -> None:
            await audit.add(
                action=action,
                outcome=outcome,
                user_id=job.user_id,
                device_id=job.device_id,
                target_type="schedule" if target is None else "task",
                target_id=str(job.id) if target is None else target,
            )

        device = await DeviceRepository(session).get(job.device_id) if job.device_id else None
        if device is None or device.revoked_at is not None or device.user_id != job.user_id:
            job.enabled = False
            await note("schedule.disabled", AuditOutcome.DENIED)
            return "disabled"
        if skipped_earlier:
            await note("schedule.missed", AuditOutcome.FAILURE)
        if (now - occurrence).total_seconds() > self._settings.schedule_grace_seconds:
            await note("schedule.missed", AuditOutcome.FAILURE)
            return "missed"

        await tasks.lock_user(job.user_id)
        if await tasks.count_active(job.user_id) >= self._settings.max_active_tasks_per_user:
            await note("schedule.skipped", AuditOutcome.DENIED)
            return "skipped"
        task = await tasks.create_scheduled(
            user_id=job.user_id,
            device_id=job.device_id,
            task_type=job.task_type,
            task_input=job.task_input,
            fingerprint=fingerprint_for(TaskType(job.task_type), job.task_input),
            max_attempts=self._settings.task_max_attempts,
            max_runtime_seconds=self._settings.task_max_runtime_seconds,
            schedule_id=job.id,
            scheduled_for=occurrence,
        )
        if task is None:
            return "skipped"  # the database already holds a task for this occurrence
        job.last_run_at = now
        job.last_task_id = task.id
        await note("schedule.run", target=str(task.id))
        return "dispatched"
