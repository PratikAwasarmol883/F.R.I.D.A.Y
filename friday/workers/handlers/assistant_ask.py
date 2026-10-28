from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.adapters.llm import LLMProvider
from friday.core.config import Settings
from friday.domain.exceptions import InvalidTaskInputError
from friday.domain.task_inputs import AskTaskInput
from friday.domain.task_state import TaskType
from friday.orchestration.tool_registry import ToolRegistry
from friday.services.assistant_service import AskHooks, AskResult, AssistantService
from friday.workers.context import TaskContext
from friday.workers.handlers.common import load_current_user


def result_json(result: AskResult) -> dict[str, Any]:
    """References and metadata only; the answer text lives in `messages`."""
    return {
        "conversation_id": str(result.conversation_id),
        "message_id": str(result.message_id),
        "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
        "sources": [{"title": s.title, "url": s.url} for s in result.sources],
        "tools_used": [{"name": u.name, "status": u.status.value} for u in result.tools_used],
    }


class AssistantAskHandler:
    task_type = TaskType.ASSISTANT_ASK

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        llm_factory: Callable[[], LLMProvider],
        registry_factory: Callable[[], ToolRegistry],
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._llm_factory = llm_factory
        self._registry_factory = registry_factory

    async def run(self, ctx: TaskContext) -> None:
        try:
            data = AskTaskInput.model_validate(ctx.task_input)
        except ValidationError as exc:
            raise InvalidTaskInputError from exc
        user = await load_current_user(ctx, self._session_factory)
        llm = self._llm_factory()
        registry = self._registry_factory()

        async def finish(session: AsyncSession, result: AskResult) -> None:
            await ctx.mark_succeeded(session, result_json(result))

        hooks = AskHooks(
            task_id=ctx.task_id,
            progress=ctx.emit,
            check_cancelled=ctx.check_cancelled,
            lock_for_finish=ctx.lock_for_finish,
            finish=finish,
        )
        async with self._session_factory() as session:
            service = AssistantService(session, llm, self._settings, registry)
            await service.ask(user, data.message, data.conversation_id, hooks)
