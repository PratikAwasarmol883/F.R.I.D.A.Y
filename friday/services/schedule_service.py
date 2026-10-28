import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.config import Settings
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import ForbiddenError, NotFoundError, ScheduleLimitError
from friday.domain.research import REQUIRED_SCOPES
from friday.domain.schedule import ScheduleSpec, next_occurrence, parse_timezone
from friday.domain.task_state import TaskType
from friday.persistence.models import ScheduledJob, Task
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.schedules import ScheduleRepository
from friday.services.auth_service import CurrentUser


class ScheduleService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._schedules = ScheduleRepository(session)
        self._audit = AuditRepository(session)

    async def _record(self, action: str, user: CurrentUser, schedule_id: uuid.UUID) -> None:
        await self._audit.add(
            action=action,
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="schedule",
            target_id=str(schedule_id),
        )

    async def create(
        self,
        user: CurrentUser,
        *,
        name: str,
        task_type: TaskType,
        task_input: dict[str, Any],
        spec: ScheduleSpec,
        timezone: str,
        enabled: bool,
    ) -> ScheduledJob:
        if task_type is TaskType.RESEARCH and not REQUIRED_SCOPES <= user.scopes:
            raise ForbiddenError
        if await self._schedules.count(user.user_id) >= self._settings.max_schedules_per_user:
            raise ScheduleLimitError
        job = await self._schedules.create(
            user_id=user.user_id,
            device_id=user.device_id,
            name=name,
            task_type=task_type.value,
            task_input=task_input,
            schedule=spec.model_dump(mode="json"),
            timezone=timezone,
            enabled=enabled,
            next_run_at=next_occurrence(spec, parse_timezone(timezone), datetime.now(UTC)),
        )
        await self._record("schedule.create", user, job.id)
        await self._session.commit()
        return job

    async def list_for_user(self, user: CurrentUser) -> list[ScheduledJob]:
        return await self._schedules.list_for_user(user.user_id)

    async def get(self, user: CurrentUser, schedule_id: uuid.UUID) -> ScheduledJob:
        job = await self._schedules.get_owned(schedule_id, user.user_id)
        if job is None:
            raise NotFoundError
        return job

    async def update(
        self,
        user: CurrentUser,
        schedule_id: uuid.UUID,
        *,
        name: str | None,
        enabled: bool | None,
        spec: ScheduleSpec | None,
        timezone: str | None,
    ) -> ScheduledJob:
        job = await self._schedules.get_owned(schedule_id, user.user_id, for_update=True)
        if job is None:
            raise NotFoundError
        recompute = (
            spec is not None or timezone is not None or (enabled is True and not job.enabled)
        )
        if name is not None:
            job.name = name
        if enabled is not None:
            job.enabled = enabled
        if spec is not None:
            job.schedule = spec.model_dump(mode="json")
        if timezone is not None:
            job.timezone = timezone
        if recompute:  # from now on; a changed or re-enabled schedule never catches up
            job.next_run_at = next_occurrence(
                ScheduleSpec.model_validate(job.schedule),
                parse_timezone(job.timezone),
                datetime.now(UTC),
            )
        job.updated_at = datetime.now(UTC)
        await self._record("schedule.update", user, job.id)
        await self._session.commit()
        return job

    async def delete(self, user: CurrentUser, schedule_id: uuid.UUID) -> None:
        if not await self._schedules.delete_owned(schedule_id, user.user_id):
            raise NotFoundError
        await self._record("schedule.delete", user, schedule_id)
        await self._session.commit()

    async def runs(self, user: CurrentUser, schedule_id: uuid.UUID, limit: int) -> list[Task]:
        await self.get(user, schedule_id)
        return await self._schedules.runs(schedule_id, user.user_id, limit)
