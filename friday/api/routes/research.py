from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.routes.tasks import to_task_out
from friday.api.schemas import TaskOut
from friday.core.config import Settings, get_settings
from friday.domain.exceptions import ForbiddenError, ProviderUnavailableError
from friday.domain.research import REQUIRED_SCOPES, REQUIRED_TOOLS
from friday.domain.task_inputs import ResearchTaskInput
from friday.domain.task_state import TaskType
from friday.persistence.database import get_db_session
from friday.services.auth_service import CurrentUser
from friday.services.task_service import TaskService

router = APIRouter(tags=["research"])

IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
]


@router.post("/research", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
async def start_research(
    body: ResearchTaskInput,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TaskOut:
    if not REQUIRED_SCOPES <= user.scopes:
        raise ForbiddenError
    if not settings.search_key_configured or not REQUIRED_TOOLS <= settings.enabled_tool_names:
        raise ProviderUnavailableError  # research cannot run on this deployment
    created = await TaskService(session, settings).create(
        user, TaskType.RESEARCH, body.model_dump(mode="json"), idempotency_key
    )
    if not created.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/v1/tasks/{created.task.id}"
    return to_task_out(created.task, None)
