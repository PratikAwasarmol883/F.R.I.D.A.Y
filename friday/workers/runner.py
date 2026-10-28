"""Runs one claimed task: handler + heartbeat/cancel watcher + runtime limit + outcome mapping."""

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from datetime import timedelta
from enum import StrEnum
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.core.config import Settings
from friday.core.logging_config import get_logger
from friday.domain.task_state import (
    RETRYABLE_CODES,
    TaskErrorCode,
    TaskStatus,
    backoff_seconds,
    error_code_for,
)
from friday.persistence.models import Task
from friday.persistence.repositories.devices import DeviceRepository
from friday.persistence.repositories.tasks import TaskRepository
from friday.workers.context import TaskContext
from friday.workers.errors import LeaseLostError, TaskCancelled
from friday.workers.handlers.registry import HandlerRegistry

logger = get_logger(__name__)
T = TypeVar("T")


class HeartbeatEnd(StrEnum):
    CANCEL = "cancel"
    LEASE_LOST = "lease_lost"


class TaskRun:
    def __init__(
        self,
        task: Task,
        *,
        worker_id: str,
        session_factory: async_sessionmaker[AsyncSession],
        handlers: HandlerRegistry,
        settings: Settings,
        rng: random.Random | None = None,
    ) -> None:
        self.task = task
        self.interrupted = False
        self._worker_id = worker_id
        self._session_factory = session_factory
        self._handlers = handlers
        self._settings = settings
        self._rng = rng
        self._handler_task: asyncio.Task[None] | None = None

    def interrupt(self) -> None:
        """Graceful-shutdown request: stop the handler; execute() then releases the task."""
        self.interrupted = True
        if self._handler_task is not None and not self._handler_task.done():
            self._handler_task.cancel()

    # ----- database helpers (each in its own short, committed transa -----

    async def _tx(self, fn: Callable[[TaskRepository], Awaitable[T]]) -> T:
        async with self._session_factory() as session:
            result = await fn(TaskRepository(session))
            await session.commit()
            return result

    async def _finish(self, target: TaskStatus, code: TaskErrorCode | None = None) -> str:
        task = self.task
        outcome = await self._tx(
            lambda r: r.finish(task.id, self._worker_id, task.attempt, target, error_code=code)
        )
        return self._label(outcome)

    def _label(self, outcome: TaskStatus | None) -> str:
        if outcome is None:
            logger.warning(
                "Lease lost before the outcome could be recorded", extra={"task_id": self.task.id}
            )
            return "lease_lost"
        return outcome.value

    # ----- main flow -----

    async def execute(self) -> str:
        task = self.task
        handler = self._handlers.get(task.type)
        if handler is None:
            return await self._finish(TaskStatus.FAILED, TaskErrorCode.INVALID_INPUT)
        if not await self._device_authorized():
            return await self._finish(TaskStatus.CANCELLED, TaskErrorCode.DEVICE_REVOKED)

        ctx = TaskContext(
            task_id=task.id,
            user_id=task.user_id,
            device_id=task.device_id,
            attempt=task.attempt,
            worker_id=self._worker_id,
            task_input=task.input,
            session_factory=self._session_factory,
        )
        handler_task = asyncio.create_task(handler.run(ctx), name=f"task-{task.id}")
        self._handler_task = handler_task
        heartbeat = asyncio.create_task(self._heartbeat(), name=f"heartbeat-{task.id}")
        try:
            done, _ = await asyncio.wait(
                {handler_task, heartbeat},
                timeout=task.max_runtime_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                await self._stop(handler_task, heartbeat)
                return await self._finish(TaskStatus.TIMED_OUT, TaskErrorCode.TIMED_OUT)
            if heartbeat in done:
                await self._stop(handler_task)
                end = heartbeat.result()
                if end is HeartbeatEnd.CANCEL:
                    return await self._finish(TaskStatus.CANCELLED)
                logger.warning("Lease lost; abandoning task", extra={"task_id": task.id})
                return "lease_lost"
            await self._stop(heartbeat)
            return await self._on_handler_done(handler_task)
        except asyncio.CancelledError:
            await self._stop(handler_task, heartbeat)
            raise

    @staticmethod
    async def _stop(*tasks: asyncio.Task[Any]) -> None:
        for item in tasks:
            if not item.done():
                item.cancel()
        for item in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await item

    async def _device_authorized(self) -> bool:
        task = self.task
        if task.device_id is None:
            return False
        async with self._session_factory() as session:
            device = await DeviceRepository(session).get(task.device_id)
        return device is not None and device.revoked_at is None and device.user_id == task.user_id

    async def _heartbeat(self) -> HeartbeatEnd:
        task, lease = self.task, timedelta(seconds=self._settings.task_lease_seconds)
        while True:
            await asyncio.sleep(self._settings.task_heartbeat_seconds)
            try:
                beat = await self._tx(
                    lambda r: r.heartbeat(task.id, self._worker_id, task.attempt, lease)
                )
            except Exception:
                logger.warning("Heartbeat failed; will retry", extra={"task_id": task.id})
                continue
            if beat is None:
                return HeartbeatEnd.LEASE_LOST
            if beat.cancel_requested:
                return HeartbeatEnd.CANCEL

    async def _on_handler_done(self, handler_task: asyncio.Task[None]) -> str:
        task = self.task
        if handler_task.cancelled():
            if self.interrupted:
                released = await self._tx(
                    lambda r: r.release(task.id, self._worker_id, task.attempt)
                )
                return "released" if released is not None else "lease_lost"
            return await self._finish(TaskStatus.FAILED, TaskErrorCode.INTERNAL_ERROR)
        exc = handler_task.exception()
        if exc is None:
            return await self._confirm_success()
        if isinstance(exc, TaskCancelled):
            return await self._finish(TaskStatus.CANCELLED)
        if isinstance(exc, LeaseLostError):
            logger.warning("Lease lost during run", extra={"task_id": task.id})
            return "lease_lost"
        return await self._on_failure(exc)

    async def _confirm_success(self) -> str:
        """A handler must mark success itself (atomically with its effects); verify it did."""
        task = self.task
        status = await self._tx(lambda r: r.current_status(task.id))
        if status is TaskStatus.SUCCEEDED:
            return "succeeded"
        logger.error("Handler returned without completing the task", extra={"task_id": task.id})
        return await self._finish(TaskStatus.FAILED, TaskErrorCode.INTERNAL_ERROR)

    async def _on_failure(self, exc: BaseException) -> str:
        task = self.task
        code = error_code_for(exc)
        if code is TaskErrorCode.INTERNAL_ERROR:
            logger.error("Task crashed", exc_info=exc, extra={"task_id": task.id})
        else:
            logger.warning("Task attempt failed", extra={"task_id": task.id, "outcome": code.value})
        if code in RETRYABLE_CODES and task.attempt < task.max_attempts:
            delay = backoff_seconds(
                task.attempt,
                self._settings.task_retry_base_seconds,
                self._settings.task_retry_max_seconds,
                self._rng,
            )
            outcome = await self._tx(
                lambda r: r.schedule_retry(
                    task.id, self._worker_id, task.attempt, code=code, delay_seconds=delay
                )
            )
            return self._label(outcome)
        return await self._finish(TaskStatus.FAILED, code)
