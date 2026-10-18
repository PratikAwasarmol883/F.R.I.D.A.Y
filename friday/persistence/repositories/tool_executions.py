import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.enums import ToolStatus
from friday.persistence.models import ToolExecution


class ToolExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        tool_name: str,
        tool_version: str,
        risk_class: str | None,
        status: ToolStatus,
        arguments_redacted: dict[str, Any],
        arguments_hash: str,
        result_meta: dict[str, Any] | None,
        error_code: str | None,
        duration_ms: int,
    ) -> ToolExecution:
        row = ToolExecution(
            id=uuid.uuid4(),
            user_id=user_id,
            device_id=device_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            tool_version=tool_version,
            risk_class=risk_class,
            status=str(status),
            arguments_redacted=arguments_redacted,
            arguments_hash=arguments_hash,
            result_meta=result_meta,
            error_code=error_code,
            duration_ms=duration_ms,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def counts_since(self, user_id: uuid.UUID, since: datetime) -> dict[str, int]:
        """Per-tool count of calls that actually ran (denied calls do not consume quota)."""
        stmt = (
            select(ToolExecution.tool_name, func.count())
            .where(
                ToolExecution.user_id == user_id,
                ToolExecution.created_at >= since,
                ToolExecution.status != str(ToolStatus.DENIED),
            )
            .group_by(ToolExecution.tool_name)
        )
        return {name: int(total) for name, total in (await self._session.execute(stmt)).all()}

    async def attach_to_message(
        self, execution_ids: Sequence[uuid.UUID], conversation_id: uuid.UUID, message_id: uuid.UUID
    ) -> None:
        if not execution_ids:
            return
        await self._session.execute(
            update(ToolExecution)
            .where(ToolExecution.id.in_(execution_ids))
            .values(conversation_id=conversation_id, message_id=message_id)
        )

    async def recent_for_user(self, user_id: uuid.UUID, limit: int) -> list[ToolExecution]:
        stmt = (
            select(ToolExecution)
            .where(ToolExecution.user_id == user_id)
            .order_by(ToolExecution.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())
