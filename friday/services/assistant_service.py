import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.llm import ChatMessage, LLMProvider
from friday.core.config import Settings
from friday.core.logging_config import get_logger
from friday.domain.enums import AuditOutcome, MessageRole
from friday.domain.exceptions import BudgetExceededError, NotFoundError, ProviderError
from friday.persistence.models import Conversation
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.conversations import ConversationRepository
from friday.persistence.repositories.usage import UsageRepository
from friday.services.auth_service import CurrentUser

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are FRIDAY, a personal AI assistant. Answer clearly and concisely. "
    "Be honest about uncertainty and say when you do not know. "
    "You currently have no tools, no web access, and no memory beyond this conversation; "
    "do not claim otherwise."
)


@dataclass(frozen=True)
class AskResult:
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    input_tokens: int
    output_tokens: int


class AssistantService:
    def __init__(self, session: AsyncSession, llm: LLMProvider, settings: Settings) -> None:
        self._session = session
        self._llm = llm
        self._settings = settings
        self._conversations = ConversationRepository(session)
        self._usage = UsageRepository(session)
        self._audit = AuditRepository(session)

    async def ask(
        self, user: CurrentUser, message: str, conversation_id: uuid.UUID | None
    ) -> AskResult:
        history = await self._load_history(user, conversation_id)
        try:
            await self._ensure_within_budget(user)
            await self._session.commit()  # no DB transaction is held across the LLM call
            started = time.perf_counter()
            result = await self._llm.complete(
                system=SYSTEM_PROMPT,
                messages=[*history, ChatMessage(MessageRole.USER, message)],
                max_output_tokens=self._settings.llm_max_output_tokens,
                timeout_seconds=self._settings.llm_timeout_seconds,
            )
        except (BudgetExceededError, ProviderError) as exc:
            await self._record_failure(user, conversation_id, exc)
            raise
        latency_ms = int((time.perf_counter() - started) * 1000)

        conversation = await self._write_conversation(user, conversation_id)
        sequence = await self._conversations.next_sequence(conversation.id)
        await self._conversations.add_message(conversation.id, sequence, MessageRole.USER, message)
        reply = await self._conversations.add_message(
            conversation.id, sequence + 1, MessageRole.ASSISTANT, result.text
        )
        await self._usage.add(
            user_id=user.user_id,
            conversation_id=conversation.id,
            message_id=reply.id,
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        await self._conversations.touch(conversation.id)
        await self._audit.add(
            action="assistant.ask",
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="conversation",
            target_id=str(conversation.id),
        )
        await self._session.commit()

        logger.info(
            "assistant.ask",
            extra={
                "user_id": user.user_id,
                "conversation_id": conversation.id,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": latency_ms,
                "outcome": "success",
            },
        )
        return AskResult(
            conversation_id=conversation.id,
            message_id=reply.id,
            answer=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    async def _load_history(
        self, user: CurrentUser, conversation_id: uuid.UUID | None
    ) -> list[ChatMessage]:
        if conversation_id is None:
            return []
        conversation = await self._conversations.get_owned(conversation_id, user.user_id)
        if conversation is None:
            raise NotFoundError
        rows = await self._conversations.recent_messages(
            conversation.id, self._settings.max_history_messages
        )
        history = [ChatMessage(row.role, row.content) for row in rows]
        while history and history[0].role != MessageRole.USER:
            history.pop(0)  # providers require the first message to come from the user
        return history

    async def _ensure_within_budget(self, user: CurrentUser) -> None:
        start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        used = await self._usage.tokens_used_since(user.user_id, start_of_day)
        if used >= self._settings.daily_token_budget:
            raise BudgetExceededError

    async def _write_conversation(
        self, user: CurrentUser, conversation_id: uuid.UUID | None
    ) -> Conversation:
        if conversation_id is None:
            return await self._conversations.create(user.user_id, None)
        conversation = await self._conversations.get_owned(
            conversation_id, user.user_id, for_update=True
        )
        if conversation is None:
            raise NotFoundError
        return conversation

    async def _record_failure(
        self, user: CurrentUser, conversation_id: uuid.UUID | None, exc: Exception
    ) -> None:
        """Persist only an audit row; the user's message is deliberately not stored."""
        outcome = (
            AuditOutcome.DENIED if isinstance(exc, BudgetExceededError) else AuditOutcome.FAILURE
        )
        try:
            await self._session.rollback()
            await self._audit.add(
                action="assistant.ask",
                outcome=outcome,
                user_id=user.user_id,
                device_id=user.device_id,
                target_type="conversation" if conversation_id else None,
                target_id=str(conversation_id) if conversation_id else None,
            )
            await self._session.commit()
        except Exception:
            logger.exception("Failed to record audit event for failed ask")
        logger.warning(
            "assistant.ask", extra={"user_id": user.user_id, "outcome": type(exc).__name__}
        )
