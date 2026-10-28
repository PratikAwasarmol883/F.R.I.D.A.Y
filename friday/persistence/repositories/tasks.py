"""Task queue persistence.

Every method that mutates a leased task is *fenced* by (lease_owner, attempt): if another worker
has taken over (or the reaper requeued the task) the fence no longer matches, the write affects
nothing, and the caller learns it lost the lease. Methods never commit; callers own the transaction.
All time comparisons use database time.
"""

import json
import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.redaction import redact_value
from friday.domain.enums import AuditOutcome
from friday.domain.exceptions import NotFoundError, TaskNotCancellableError
from friday.domain.task_state import (
    LEASED_STATUSES,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    TaskErrorCode,
    TaskEventType,
    TaskStatus,
    assert_transition,
    backoff_seconds,
)
from friday.persistence.models import Task, TaskEvent
from friday.persistence.repositories.audit import AuditRepository

MAX_EVENT_PAYLOAD_CHARS = 1024
_ACTIVE = [s.value for s in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)]
_LEASED = [s.value for s in LEASED_STATUSES]
_TERMINAL = [s.value for s in TERMINAL_STATUSES]


@dataclass(frozen=True)
class Heartbeat:
    cancel_requested: bool


@dataclass(frozen=True)
class CancelResult:
    task: Task
    outcome: str  # "cancelled" | "cancel_requested" | "noop"


@dataclass(frozen=True)
class Reaped:
    task_id: uuid.UUID
    outcome: str  # "requeued" | "failed" | "cancelled"


def _bounded(payload: dict[str, Any] | None) -> dict[str, Any]:
    redacted = redact_value(payload or {})
    if len(json.dumps(redacted, default=str)) > MAX_EVENT_PAYLOAD_CHARS:
        return {"_truncated": True}
    return dict(redacted)


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditRepository(session)

    # ----- creation and lookup -------------------------------------------------------------

    async def lock_user(self, user_id: uuid.UUID) -> None:
        """Serialize check-then-insert per user (limit enforcement) until commit."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"tasks:{user_id}"},
        )

    async def count_active(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user_id, Task.status.in_(_ACTIVE))
        )
        return int(await self._session.scalar(stmt) or 0)

    async def find_by_idempotency_key(self, user_id: uuid.UUID, key: str) -> Task | None:
        stmt = select(Task).where(Task.user_id == user_id, Task.idempotency_key == key)
        return await self._session.scalar(stmt)

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        task_type: str,
        task_input: dict[str, Any],
        idempotency_key: str | None,
        fingerprint: str,
        max_attempts: int,
        max_runtime_seconds: int,
    ) -> tuple[Task, bool]:
        """Insert a task; on an idempotency-key race return the existing one with created=False."""
        stmt = (
            pg_insert(Task)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                device_id=device_id,
                type=task_type,
                status=TaskStatus.QUEUED.value,
                input=task_input,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                max_attempts=max_attempts,
                max_runtime_seconds=max_runtime_seconds,
            )
            .on_conflict_do_nothing(
                index_elements=[Task.user_id, Task.idempotency_key],
                index_where=Task.idempotency_key.is_not(None),
            )
            .returning(Task)
        )
        created = await self._session.scalar(stmt)
        if created is not None:
            await self.add_event(created.id, TaskEventType.CREATED, {"type": task_type})
            return created, True
        assert idempotency_key is not None
        existing = await self.find_by_idempotency_key(user_id, idempotency_key)
        assert existing is not None
        return existing, False

    async def create_scheduled(
        self,
        *,
        user_id: uuid.UUID,
        device_id: uuid.UUID | None,
        task_type: str,
        task_input: dict[str, Any],
        fingerprint: str,
        max_attempts: int,
        max_runtime_seconds: int,
        schedule_id: uuid.UUID,
        scheduled_for: datetime,
    ) -> Task | None:
        """One task per (schedule, occurrence); None means another dispatcher already made it."""
        stmt = (
            pg_insert(Task)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                device_id=device_id,
                type=task_type,
                status=TaskStatus.QUEUED.value,
                input=task_input,
                request_fingerprint=fingerprint,
                max_attempts=max_attempts,
                max_runtime_seconds=max_runtime_seconds,
                schedule_id=schedule_id,
                scheduled_for=scheduled_for,
            )
            .on_conflict_do_nothing(
                index_elements=[Task.schedule_id, Task.scheduled_for],
                index_where=Task.schedule_id.is_not(None),
            )
            .returning(Task)
        )
        created = await self._session.scalar(stmt)
        if created is not None:
            await self.add_event(
                created.id, TaskEventType.CREATED, {"type": task_type, "scheduled": True}
            )
        return created

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._session.scalar(
            select(Task).where(Task.id == task_id).execution_options(populate_existing=True)
        )

    async def get_owned(
        self, task_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
    ) -> Task | None:
        stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id)
        stmt = stmt.execution_options(populate_existing=True)
        if for_update:
            stmt = stmt.with_for_update()
        return await self._session.scalar(stmt)

    async def list_for_user(
        self, user_id: uuid.UUID, status: TaskStatus | None, limit: int
    ) -> list[Task]:
        stmt = select(Task).where(Task.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Task.status == status.value)
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    # ----- events ---------------------------------------------------------------------------

    async def add_event(
        self, task_id: uuid.UUID, event_type: TaskEventType, payload: dict[str, Any] | None = None
    ) -> int:
        """Atomic per-task sequence: concurrent writers serialize on the task row."""
        sequence = await self._session.scalar(
            update(Task)
            .where(Task.id == task_id)
            .values(event_seq=Task.event_seq + 1)
            .returning(Task.event_seq)
        )
        if sequence is None:
            raise NotFoundError
        self._session.add(
            TaskEvent(
                id=uuid.uuid4(),
                task_id=task_id,
                sequence=sequence,
                event_type=event_type.value,
                payload=_bounded(payload),
            )
        )
        await self._session.flush()
        return int(sequence)

    async def events_after(
        self, task_id: uuid.UUID, after_sequence: int, limit: int
    ) -> list[TaskEvent]:
        stmt = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after_sequence)
            .order_by(TaskEvent.sequence)
            .limit(limit)
        )
        return list((await self._session.scalars(stmt)).all())

    # ----- claiming -------------------------------------------------------------------------

    async def claim(self, worker_id: str, limit: int, lease: timedelta) -> list[Task]:
        if limit <= 0:
            return []
        due = (
            select(Task.id)
            .where(Task.status == TaskStatus.QUEUED.value, Task.run_after <= func.now())
            .order_by(Task.run_after, Task.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(Task)
            .where(Task.id.in_(due))
            .values(
                status=TaskStatus.RUNNING.value,
                attempt=Task.attempt + 1,
                lease_owner=worker_id,
                lease_expires_at=func.now() + lease,
                heartbeat_at=func.now(),
                started_at=func.coalesce(Task.started_at, func.now()),
                updated_at=func.now(),
            )
            .returning(Task)
            .execution_options(synchronize_session=False)
        )
        claimed = list((await self._session.scalars(stmt)).all())
        for task in claimed:
            await self.add_event(
                task.id, TaskEventType.CLAIMED, {"attempt": task.attempt, "worker": worker_id}
            )
        return claimed

    # ----- fenced operations ------------------------------------------------------------------

    async def lock_fenced(self, task_id: uuid.UUID, worker_id: str, attempt: int) -> Task | None:
        """Row lock held until commit; None means this worker no longer holds the lease."""
        stmt = (
            select(Task)
            .where(
                Task.id == task_id,
                Task.lease_owner == worker_id,
                Task.attempt == attempt,
                Task.status.in_(_LEASED),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(stmt)

    async def heartbeat(
        self, task_id: uuid.UUID, worker_id: str, attempt: int, lease: timedelta
    ) -> Heartbeat | None:
        stmt = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.lease_owner == worker_id,
                Task.attempt == attempt,
                Task.status.in_(_LEASED),
            )
            .values(
                lease_expires_at=func.now() + lease, heartbeat_at=func.now(), updated_at=func.now()
            )
            .returning(Task.status)
            .execution_options(synchronize_session=False)
        )
        status = await self._session.scalar(stmt)
        if status is None:
            return None
        return Heartbeat(cancel_requested=status == TaskStatus.CANCEL_REQUESTED.value)

    async def current_status(self, task_id: uuid.UUID) -> TaskStatus | None:
        status = await self._session.scalar(select(Task.status).where(Task.id == task_id))
        return TaskStatus(status) if status else None

    async def finish(
        self,
        task_id: uuid.UUID,
        worker_id: str,
        attempt: int,
        target: TaskStatus,
        *,
        error_code: TaskErrorCode | None = None,
        result: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TaskStatus | None:
        """Fenced terminal transition. A pending cancel always wins over any other target."""
        task = await self.lock_fenced(task_id, worker_id, attempt)
        if task is None:
            return None
        return await self._apply_terminal(task, target, error_code, result, payload)

    async def _apply_terminal(
        self,
        task: Task,
        target: TaskStatus,
        error_code: TaskErrorCode | None,
        result: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> TaskStatus:
        if target not in TERMINAL_STATUSES:
            raise InvalidTransitionError(f"{target.value} is not a terminal status")
        current = TaskStatus(task.status)
        if current is TaskStatus.CANCEL_REQUESTED and target is not TaskStatus.CANCELLED:
            target, error_code, result = TaskStatus.CANCELLED, None, None
        assert_transition(current, target)
        await self._session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(
                status=target.value,
                error_code=error_code.value if error_code else None,
                result=result,
                finished_at=func.now(),
                lease_expires_at=None,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        event = TaskEventType(target.value)
        details = {"attempt": task.attempt, **(payload or {})}
        if error_code:
            details["code"] = error_code.value
        await self.add_event(task.id, event, details)
        await self._audit.add(
            action="task.finish",
            outcome=_audit_outcome(target, error_code),
            user_id=task.user_id,
            device_id=task.device_id,
            target_type="task",
            target_id=str(task.id),
        )
        return target

    async def schedule_retry(
        self,
        task_id: uuid.UUID,
        worker_id: str,
        attempt: int,
        *,
        code: TaskErrorCode,
        delay_seconds: float,
    ) -> TaskStatus | None:
        task = await self.lock_fenced(task_id, worker_id, attempt)
        if task is None:
            return None
        if TaskStatus(task.status) is TaskStatus.CANCEL_REQUESTED:
            return await self._apply_terminal(task, TaskStatus.CANCELLED, None, None, None)
        await self._requeue(task, code, delay_seconds, TaskEventType.RETRY_SCHEDULED)
        return TaskStatus.QUEUED

    async def release(self, task_id: uuid.UUID, worker_id: str, attempt: int) -> TaskStatus | None:
        """Graceful shutdown: hand the task back without consuming the attempt."""
        task = await self.lock_fenced(task_id, worker_id, attempt)
        if task is None:
            return None
        if TaskStatus(task.status) is TaskStatus.CANCEL_REQUESTED:
            return await self._apply_terminal(task, TaskStatus.CANCELLED, None, None, None)
        await self._requeue(task, None, 0, TaskEventType.RELEASED, consume_attempt=False)
        return TaskStatus.QUEUED

    async def _requeue(
        self,
        task: Task,
        code: TaskErrorCode | None,
        delay_seconds: float,
        event: TaskEventType,
        *,
        consume_attempt: bool = True,
    ) -> None:
        assert_transition(TaskStatus(task.status), TaskStatus.QUEUED)
        await self._session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(
                status=TaskStatus.QUEUED.value,
                attempt=task.attempt if consume_attempt else max(0, task.attempt - 1),
                run_after=func.now() + timedelta(seconds=delay_seconds),
                error_code=code.value if code else None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        details: dict[str, Any] = {
            "attempt": task.attempt,
            "delay_seconds": round(delay_seconds, 3),
        }
        if code:
            details["code"] = code.value
        await self.add_event(task.id, event, details)

    # ----- cancellation (API side) ---------------------------------------------------------------

    async def request_cancel(self, task_id: uuid.UUID, user_id: uuid.UUID) -> CancelResult:
        task = await self.get_owned(task_id, user_id, for_update=True)
        if task is None:
            raise NotFoundError
        status = TaskStatus(task.status)
        if status in {TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELLED}:
            return CancelResult(task, "noop")
        if status is TaskStatus.QUEUED:
            await self._apply_terminal(task, TaskStatus.CANCELLED, None, None, {"by": "user"})
            await self._session.refresh(task)
            return CancelResult(task, "cancelled")
        if status is TaskStatus.RUNNING:
            assert_transition(status, TaskStatus.CANCEL_REQUESTED)
            await self._session.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(
                    status=TaskStatus.CANCEL_REQUESTED.value,
                    cancel_requested_at=func.now(),
                    updated_at=func.now(),
                )
                .execution_options(synchronize_session=False)
            )
            await self.add_event(task.id, TaskEventType.CANCEL_REQUESTED, {"attempt": task.attempt})
            await self._audit.add(
                action="task.cancel",
                outcome=AuditOutcome.SUCCESS,
                user_id=task.user_id,
                device_id=task.device_id,
                target_type="task",
                target_id=str(task.id),
            )
            await self._session.refresh(task)
            return CancelResult(task, "cancel_requested")
        raise TaskNotCancellableError

    # ----- recovery, operator tools, retention -----

    async def reap_expired(
        self,
        limit: int,
        *,
        retry_base: float,
        retry_cap: float,
        rng: random.Random | None = None,
    ) -> list[Reaped]:
        """Recover tasks whose worker stopped heartbeating; safe from many workers at once."""
        stmt = (
            select(Task)
            .where(Task.status.in_(_LEASED), Task.lease_expires_at < func.now())
            .order_by(Task.lease_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        reaped: list[Reaped] = []
        for task in (await self._session.scalars(stmt)).all():
            if TaskStatus(task.status) is TaskStatus.CANCEL_REQUESTED:
                await self._apply_terminal(task, TaskStatus.CANCELLED, None, None, {"by": "reaper"})
                reaped.append(Reaped(task.id, "cancelled"))
            elif task.attempt < task.max_attempts:
                delay = backoff_seconds(task.attempt, retry_base, retry_cap, rng)
                await self._requeue(
                    task, TaskErrorCode.WORKER_LOST, delay, TaskEventType.LEASE_EXPIRED_REQUEUED
                )
                reaped.append(Reaped(task.id, "requeued"))
            else:
                await self._apply_terminal(
                    task, TaskStatus.FAILED, TaskErrorCode.WORKER_LOST, None, {"by": "reaper"}
                )
                reaped.append(Reaped(task.id, "failed"))
        return reaped

    async def requeue_by_operator(self, task_id: uuid.UUID) -> Task:
        task = await self._session.scalar(
            select(Task)
            .where(Task.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            raise NotFoundError
        if TaskStatus(task.status) not in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
            raise InvalidTransitionError("only failed or timed-out tasks can be requeued")
        assert_transition(TaskStatus(task.status), TaskStatus.QUEUED, operator=True)
        await self._session.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(
                status=TaskStatus.QUEUED.value,
                attempt=0,
                run_after=func.now(),
                error_code=None,
                result=None,
                finished_at=None,
                cancel_requested_at=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        await self.add_event(task.id, TaskEventType.REQUEUED_BY_OPERATOR, {})
        await self._audit.add(
            action="task.requeue",
            outcome=AuditOutcome.SUCCESS,
            user_id=task.user_id,
            target_type="task",
            target_id=str(task.id),
        )
        await self._session.refresh(task)
        return task

    async def purge_terminal_older_than(self, days: int) -> int:
        result = await self._session.execute(
            delete(Task).where(
                Task.status.in_(_TERMINAL),
                Task.finished_at < func.now() - timedelta(days=days),
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def bulk_status(self, task_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        rows = await self._session.execute(
            select(Task.id, Task.status).where(Task.id.in_(list(task_ids)))
        )
        return dict(rows.all())


def _audit_outcome(target: TaskStatus, code: TaskErrorCode | None) -> AuditOutcome:
    if target in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}:
        return AuditOutcome.FAILURE
    if target is TaskStatus.CANCELLED and code is TaskErrorCode.DEVICE_REVOKED:
        return AuditOutcome.DENIED
    return AuditOutcome.SUCCESS
