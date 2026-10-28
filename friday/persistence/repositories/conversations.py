import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import Conversation, Message


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: uuid.UUID, title: str | None) -> Conversation:
        conversation = Conversation(id=uuid.uuid4(), user_id=user_id, title=title)
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get_owned(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Conversation | None:
        """Ownership is enforced in SQL; callers never see another user's conversation."""
        stmt = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def recent_messages(self, conversation_id: uuid.UUID, limit: int) -> list[Message]:
        """Most recent `limit` messages in ascending order."""
        if limit <= 0:
            return []
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence.desc())
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        return list(reversed(rows))

    async def messages_page(
        self, conversation_id: uuid.UUID, limit: int, before_sequence: int | None
    ) -> tuple[list[Message], bool]:
        """Latest `limit` messages below `before_sequence`, ascending, plus whether older exist."""
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if before_sequence is not None:
            stmt = stmt.where(Message.sequence < before_sequence)
        stmt = stmt.order_by(Message.sequence.desc()).limit(limit + 1)
        rows = list((await self._session.scalars(stmt)).all())
        has_more = len(rows) > limit
        return list(reversed(rows[:limit])), has_more

    async def next_sequence(self, conversation_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Message.sequence), 0) + 1).where(
            Message.conversation_id == conversation_id
        )
        return int(await self._session.scalar(stmt) or 1)

    async def add_message(
        self, conversation_id: uuid.UUID, sequence: int, role: str, content: str
    ) -> Message:
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            sequence=sequence,
            role=role,
            content=content,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def touch(self, conversation_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )

    async def get_message_owned(self, message_id: uuid.UUID, user_id: uuid.UUID) -> Message | None:
        stmt = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.id == message_id, Conversation.user_id == user_id)
        )
        return await self._session.scalar(stmt)
