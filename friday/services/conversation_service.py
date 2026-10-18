import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from friday.domain.exceptions import NotFoundError
from friday.persistence.models import Conversation, Message
from friday.persistence.repositories.conversations import ConversationRepository
from friday.services.auth_service import CurrentUser


@dataclass(frozen=True)
class ConversationPage:
    conversation: Conversation
    messages: list[Message]
    next_before_sequence: int | None


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._conversations = ConversationRepository(session)

    async def create(self, user: CurrentUser, title: str | None) -> Conversation:
        conversation = await self._conversations.create(user.user_id, title)
        await self._session.commit()
        return conversation

    async def get_page(
        self,
        user: CurrentUser,
        conversation_id: uuid.UUID,
        limit: int,
        before_sequence: int | None,
    ) -> ConversationPage:
        conversation = await self._conversations.get_owned(conversation_id, user.user_id)
        if conversation is None:
            raise NotFoundError
        messages, has_more = await self._conversations.messages_page(
            conversation.id, limit, before_sequence
        )
        next_before = messages[0].sequence if has_more and messages else None
        return ConversationPage(conversation, messages, next_before)
