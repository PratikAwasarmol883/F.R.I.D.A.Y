import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.schemas import MemoryCreate, MemoryExport, MemoryOut, MemoryUpdate
from friday.core.config import Settings, get_settings
from friday.domain.exceptions import MemoryRejectedError
from friday.persistence.database import get_db_session
from friday.services.auth_service import CurrentUser
from friday.services.memory_service import MemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


def _service(session: AsyncSession, settings: Settings) -> MemoryService:
    return MemoryService(session, settings)


@router.get("", response_model=list[MemoryOut])
async def list_memory(
    status_filter: Literal["pending", "approved"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[MemoryOut]:
    rows = await _service(session, settings).list_for_user(user, status_filter, limit)
    return [MemoryOut.model_validate(row) for row in rows]


@router.get("/export", response_model=MemoryExport)
async def export_memory(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MemoryExport:
    rows = await _service(session, settings).export(user)
    return MemoryExport(
        exported_at=datetime.now(UTC), memories=[MemoryOut.model_validate(r) for r in rows]
    )


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MemoryOut:
    memory = await _service(session, settings).create(user, body.kind, body.value)
    return MemoryOut.model_validate(memory)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MemoryOut:
    if body.kind is None and body.value is None:
        raise MemoryRejectedError("empty")
    memory = await _service(session, settings).update(user, memory_id, body.kind, body.value)
    return MemoryOut.model_validate(memory)


@router.post("/{memory_id}/approve", response_model=MemoryOut)
async def approve_memory(
    memory_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> MemoryOut:
    return MemoryOut.model_validate(await _service(session, settings).approve(user, memory_id))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    await _service(session, settings).delete(user, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
