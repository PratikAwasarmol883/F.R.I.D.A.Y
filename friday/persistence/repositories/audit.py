import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.request_context import request_id_var
from friday.domain.enums import AuditOutcome
from friday.persistence.models import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
