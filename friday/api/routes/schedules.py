import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.schemas import (
    ScheduleCreate,
    ScheduleOut,
    ScheduleRunOut,
    ScheduleTaskOut,
    ScheduleUpdate,
)
from friday.core.config import Settings, get_settings
from friday.domain.schedule import ScheduleSpec
from friday.domain.task_state import TaskType
from friday.persistence.database import get_db_session
from friday.persistence.models import ScheduledJob
from friday.services.auth_service import CurrentUser
from friday.services.schedule_service import ScheduleService

router = APIRouter(prefix="/schedules", tags=["schedules"])

_RESULT_KEYS = ("report_id", "conversation_id", "message_id")


def to_schedule_out(job: ScheduledJob) -> ScheduleOut:
    return ScheduleOut(
        id=job.id,
        name=job.name,
        task=ScheduleTaskOut(type=job.task_type, input=job.task_input),
        schedule=ScheduleSpec.model_validate(job.schedule),
        timezone=job.timezone,
        enabled=job.enabled,
        next_run_at=job.next_run_at,
        last_run_at=job.last_run_at,
        last_task_id=job.last_task_id,
        created_at=job.created_at,
    )


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: ScheduleCreate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ScheduleOut:
    job = await ScheduleService(session, settings).create(
        user,
        name=body.name,
        task_type=TaskType(body.task.type),
        task_input=body.task.input.model_dump(mode="json"),
        spec=body.schedule,
        timezone=body.timezone,
        enabled=body.enabled,
    )
    return to_schedule_out(job)


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[ScheduleOut]:
    return [
        to_schedule_out(j) for j in await ScheduleService(session, settings).list_for_user(user)
    ]


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(
    schedule_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ScheduleOut:
    return to_schedule_out(await ScheduleService(session, settings).get(user, schedule_id))


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ScheduleOut:
    job = await ScheduleService(session, settings).update(
        user,
        schedule_id,
        name=body.name,
        enabled=body.enabled,
        spec=body.schedule,
        timezone=body.timezone,
    )
    return to_schedule_out(job)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    await ScheduleService(session, settings).delete(user, schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{schedule_id}/runs", response_model=list[ScheduleRunOut])
async def schedule_runs(
    schedule_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[ScheduleRunOut]:
    tasks = await ScheduleService(session, settings).runs(user, schedule_id, limit)
    return [
        ScheduleRunOut(
            task_id=t.id,
            status=t.status,
            scheduled_for=t.scheduled_for,
            finished_at=t.finished_at,
            error_code=t.error_code,
            result={k: v for k, v in (t.result or {}).items() if k in _RESULT_KEYS} or None,
        )
        for t in tasks
    ]
