import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.config import Settings
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import IdempotencyConflictError, NotFoundError, TaskLimitError
from friday.domain.task_state import TaskStatus, TaskType
from friday.persistence.models import Task, TaskEvent
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.conversations import ConversationRepository
from friday.persistence.repositories.tasks import CancelResult, TaskRepository
from friday.services.auth_service import CurrentUser


def fingerprint_for(task_type: TaskType, task_input: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": task_type.value, "input": task_input}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class TaskCreateResult:
    task: Task
    created: bool


@dataclass(frozen=True)
class TaskView:
    task: Task
    answer: str | None


@dataclass(frozen=True)
class EventsPage:
    events: list[TaskEvent]
    next_after_sequence: int


class TaskService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._tasks = TaskRepository(session)
        self._conversations = ConversationRepository(session)
        self._audit = AuditRepository(session)

    async def create(
        self,
        user: CurrentUser,
        task_type: TaskType,
        task_input: dict[str, Any],
        idempotency_key: str | None,
    ) -> TaskCreateResult:
        fingerprint = fingerprint_for(task_type, task_input)
        conversation_id = task_input.get("conversation_id")
        if conversation_id is not None:
            owned = await self._conversations.get_owned(uuid.UUID(conversation_id), user.user_id)
            if owned is None:
                raise NotFoundError

        await self._tasks.lock_user(user.user_id)  # serializes limit check + insert per user
        if idempotency_key is not None:
            existing = await self._tasks.find_by_idempotency_key(user.user_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, fingerprint)
        if await self._tasks.count_active(user.user_id) >= self._settings.max_active_tasks_per_user:
            raise TaskLimitError

        task, created = await self._tasks.create(
            user_id=user.user_id,
            device_id=user.device_id,
            task_type=task_type.value,
            task_input=task_input,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            max_attempts=self._settings.task_max_attempts,
            max_runtime_seconds=self._settings.task_max_runtime_seconds,
        )
        if not created:
            return self._replay(task, fingerprint)
        await self._audit.add(
            action="task.create",
            outcome=AuditOutcome.SUCCESS,
            user_id=user.user_id,
            device_id=user.device_id,
            target_type="task",
            target_id=str(task.id),
        )
        await self._session.commit()
        return TaskCreateResult(task, True)

    @staticmethod
    def _replay(existing: Task, fingerprint: str) -> TaskCreateResult:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflictError
        return TaskCreateResult(existing, False)

    async def get(self, user: CurrentUser, task_id: uuid.UUID) -> TaskView:
        task = await self._tasks.get_owned(task_id, user.user_id)
        if task is None:
            raise NotFoundError
        answer = None
        if task.status == TaskStatus.SUCCEEDED.value and task.result:
            message = await self._conversations.get_message_owned(
                uuid.UUID(task.result["message_id"]), user.user_id
            )
            answer = message.content if message else None
        return TaskView(task, answer)

    async def cancel(self, user: CurrentUser, task_id: uuid.UUID) -> CancelResult:
        result = await self._tasks.request_cancel(task_id, user.user_id)
        await self._session.commit()
        return result

    async def events(
        self, user: CurrentUser, task_id: uuid.UUID, after_sequence: int, limit: int
    ) -> EventsPage:
        task = await self._tasks.get_owned(task_id, user.user_id)
        if task is None:
            raise NotFoundError
        events = await self._tasks.events_after(task.id, after_sequence, limit)
        return EventsPage(events, events[-1].sequence if events else after_sequence)
