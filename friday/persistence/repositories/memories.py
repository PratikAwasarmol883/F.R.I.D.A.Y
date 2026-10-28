import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from friday.orchestration.memory import MemoryNote
from friday.persistence.models import Memory


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        kind: str,
        value: str,
        status: str,
        source: str,
        conversation_id: uuid.UUID | None = None,
    ) -> Memory:
        memory = Memory(
            id=uuid.uuid4(),
            user_id=user_id,
            kind=kind,
            value=value,
            status=status,
            source=source,
            conversation_id=conversation_id,
        )
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def get_owned(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> Memory | None:
        stmt = (
            select(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def list_for_user(
        self, user_id: uuid.UUID, status: str | None, limit: int
    ) -> list[Memory]:
        stmt = select(Memory).where(Memory.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Memory.status == status)
        stmt = stmt.order_by(Memory.updated_at.desc(), Memory.id).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    async def count(self, user_id: uuid.UUID, status: str | None = None) -> int:
        stmt = select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Memory.status == status)
        return int(await self._session.scalar(stmt) or 0)

    async def touch(self, memory: Memory) -> None:
        memory.updated_at = func.now()
        await self._session.flush()
        await self._session.refresh(memory)

    async def delete_owned(self, memory_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )
        return bool(getattr(result, "rowcount", 0))

    async def approved_notes(self, user_id: uuid.UUID, limit: int = 200) -> list[MemoryNote]:
        rows = await self.list_for_user(user_id, "approved", limit)
        return [MemoryNote(str(m.id), m.kind, m.value, m.updated_at) for m in rows]
