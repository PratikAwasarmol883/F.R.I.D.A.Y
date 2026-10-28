import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import UsageRecord


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
        task_id: uuid.UUID | None = None,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> uuid.UUID:
        record_id = uuid.uuid4()
        self._session.add(
            UsageRecord(
                id=record_id,
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                task_id=task_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        await self._session.flush()
        return record_id

    async def attach_to_message(
        self, usage_ids: Sequence[uuid.UUID], conversation_id: uuid.UUID, message_id: uuid.UUID
    ) -> None:
        if usage_ids:
            await self._session.execute(
                update(UsageRecord)
                .where(UsageRecord.id.in_(list(usage_ids)))
                .values(conversation_id=conversation_id, message_id=message_id)
            )

    async def tokens_used_since(self, user_id: uuid.UUID, since: datetime) -> int:
        stmt = select(
            func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0)
        ).where(UsageRecord.user_id == user_id, UsageRecord.created_at >= since)
        return int(await self._session.scalar(stmt) or 0)
