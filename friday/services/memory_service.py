import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.config import Settings
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import MemoryLimitError, NotFoundError
from friday.orchestration.memory import validate_memory_value
from friday.persistence.models import Memory
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.memories import MemoryRepository
from friday.services.auth_service import CurrentUser


class MemoryService:
    """User-approved notes. Nothing the assistant proposes counts until the user approves it."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._memories = MemoryRepository(session)
        self._audit = AuditRepository(session)

    async def _record(self, action: str, user: CurrentUser, memory_id: uuid.UUID) -> None:
        await self._audit.add(
            action=action,
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="memory",
            target_id=str(memory_id),
        )

    async def create(self, user: CurrentUser, kind: str, value: str) -> Memory:
        clean = validate_memory_value(value, allow_urls=True)
        await self._ensure_room(user, pending=False)
        memory = await self._memories.create(
            user_id=user.user_id, kind=kind, value=clean, status="approved", source="user"
        )
        await self._record("memory.create", user, memory.id)
        await self._session.commit()
        return memory

    async def propose(
        self, user: CurrentUser, kind: str, value: str, conversation_id: uuid.UUID | None = None
    ) -> Memory:
        clean = validate_memory_value(value, allow_urls=False)
        await self._ensure_room(user, pending=True)
        memory = await self._memories.create(
            user_id=user.user_id,
            kind=kind,
            value=clean,
            status="pending",
            source="assistant",
            conversation_id=conversation_id,
        )
        await self._record("memory.propose", user, memory.id)
        await self._session.commit()
        return memory

    async def _ensure_room(self, user: CurrentUser, *, pending: bool) -> None:
        if await self._memories.count(user.user_id) >= self._settings.memory_max_notes:
            raise MemoryLimitError
        if pending and (
            await self._memories.count(user.user_id, "pending") >= self._settings.memory_max_pending
        ):
            raise MemoryLimitError

    async def list_for_user(
        self, user: CurrentUser, status: str | None, limit: int
    ) -> list[Memory]:
        return await self._memories.list_for_user(user.user_id, status, limit)

    async def _owned(self, user: CurrentUser, memory_id: uuid.UUID) -> Memory:
        memory = await self._memories.get_owned(memory_id, user.user_id)
        if memory is None:
            raise NotFoundError
        return memory

    async def update(
        self, user: CurrentUser, memory_id: uuid.UUID, kind: str | None, value: str | None
    ) -> Memory:
        memory = await self._owned(user, memory_id)
        if value is not None:
            memory.value = validate_memory_value(value, allow_urls=memory.source == "user")
        if kind is not None:
            memory.kind = kind
        await self._memories.touch(memory)  # a correction never changes approval status
        await self._record("memory.update", user, memory.id)
        await self._session.commit()
        return memory

    async def approve(self, user: CurrentUser, memory_id: uuid.UUID) -> Memory:
        memory = await self._owned(user, memory_id)
        if memory.status != "approved":
            memory.status = "approved"
            await self._memories.touch(memory)
            await self._record("memory.approve", user, memory.id)
            await self._session.commit()
        return memory

    async def delete(self, user: CurrentUser, memory_id: uuid.UUID) -> None:
        """Real deletion; rejecting a proposal is the same operation."""
        if not await self._memories.delete_owned(memory_id, user.user_id):
            raise NotFoundError
        await self._record("memory.delete", user, memory_id)
        await self._session.commit()

    async def export(self, user: CurrentUser) -> list[Memory]:
        return await self._memories.list_for_user(user.user_id, None, 100_000)
