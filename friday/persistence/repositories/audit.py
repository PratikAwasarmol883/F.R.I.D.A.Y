import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.request_context import request_id_var
from friday.domain.enums import AuditOutcome
from friday.persistence.models import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_since(self, user_id: uuid.UUID, action: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.user_id == user_id,
                AuditEvent.action == action,
                AuditEvent.created_at >= since,
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def add(
        self,
        *,
        action: str,
        outcome: AuditOutcome,
        user_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> None:
        self._session.add(
            AuditEvent(
                id=uuid.uuid4(),
                user_id=user_id,
                device_id=device_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome=str(outcome),
                correlation_id=request_id_var.get(),
            )
        )
        await self._session.flush()
