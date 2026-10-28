"""The worker loop: claim, run, recover, retain, and shut down gracefully."""

import asyncio
import contextlib
import os
import random
import socket
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.core.config import Settings
from friday.core.logging_config import get_logger
from friday.persistence.models import Task
from friday.persistence.repositories.tasks import Reaped, TaskRepository
from friday.workers.handlers.registry import HandlerRegistry
from friday.workers.runner import TaskRun

logger = get_logger(__name__)

PURGE_INTERVAL_SECONDS = 3600.0
REAP_BATCH = 20
RELEASE_WAIT_SECONDS = 5.0


def new_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        handlers: HandlerRegistry,
        *,
        worker_id: str | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.worker_id = worker_id or new_worker_id()
        self._session_factory = session_factory
        self._settings = settings
        self._handlers = handlers
        self._rng = rng
        self._running: dict[uuid.UUID, TaskRun] = {}
        self._futures: set[asyncio.Task[None]] = set()
        self._stopping = asyncio.Event()

    @property
    def active(self) -> int:
        return len(self._running)

    def request_stop(self) -> None:
        self._stopping.set()

    # ----- building blocks (also driven directly by tests) -----

    async def tick(self) -> int:
        """Claim up to the free capacity and start running the claimed tasks."""
        free = self._settings.task_worker_concurrency - len(self._running)
        if free <= 0 or self._stopping.is_set():
            return 0
        async with self._session_factory() as session:
            lease = timedelta(seconds=self._settings.task_lease_seconds)
            claimed = await TaskRepository(session).claim(self.worker_id, free, lease)
            await session.commit()
        for task in claimed:
            self._start(task)
        return len(claimed)

    def _start(self, task: Task) -> None:
        run = TaskRun(
            task,
            worker_id=self.worker_id,
            session_factory=self._session_factory,
            handlers=self._handlers,
            settings=self._settings,
            rng=self._rng,
        )
        self._running[task.id] = run
        future = asyncio.create_task(self._run_one(run), name=f"run-{task.id}")
        self._futures.add(future)
        future.add_done_callback(self._futures.discard)

    async def _run_one(self, run: TaskRun) -> None:
        task_id = run.task.id
        try:
            outcome = await run.execute()
            logger.info(
                "task.finished",
                extra={"task_id": task_id, "outcome": outcome},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Task runner failed; the lease will expire and the task be recovered")
        finally:
            self._running.pop(task_id, None)

    async def reap(self) -> list[Reaped]:
        async with self._session_factory() as session:
            reaped = await TaskRepository(session).reap_expired(
                REAP_BATCH,
                retry_base=self._settings.task_retry_base_seconds,
                retry_cap=self._settings.task_retry_max_seconds,
                rng=self._rng,
            )
            await session.commit()
        for item in reaped:
            logger.warning("task.reaped", extra={"task_id": item.task_id, "outcome": item.outcome})
        return reaped

    async def purge(self) -> int:
        async with self._session_factory() as session:
            removed = await TaskRepository(session).purge_terminal_older_than(
                self._settings.task_retention_days
            )
            await session.commit()
        if removed:
            logger.info("task.purged", extra={"outcome": str(removed)})
        return removed

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """True once no task is running (used by tests and shutdown)."""
        futures = set(self._futures)
        if not futures:
            return True
        _, pending = await asyncio.wait(futures, timeout=timeout)
        return not pending

    # ----- main loop -----

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        heartbeat = self._settings.task_heartbeat_seconds
        next_reap = next_purge = 0.0
        logger.info("worker.started", extra={"outcome": self.worker_id})
        while not self._stopping.is_set():
            started = loop.time()
            try:
                if started >= next_reap:
                    await self.reap()
                    next_reap = started + heartbeat
                if started >= next_purge:
                    await self.purge()
                    next_purge = started + PURGE_INTERVAL_SECONDS
                await self.tick()
            except Exception:
                logger.exception("Worker loop error; continuing")
            interval = self._settings.task_poll_interval_seconds * (0.8 + 0.4 * random.random())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            lag = loop.time() - started - interval
            if lag > heartbeat:
                logger.warning("worker.loop_lag", extra={"latency_ms": int(lag * 1000)})
        await self._drain()
        logger.info("worker.stopped", extra={"outcome": self.worker_id})

    async def _drain(self) -> None:
        """Let in-flight tasks finish within the grace period, then hand the rest back."""
        grace = self._settings.task_shutdown_grace_seconds
        if not await self.wait_idle(grace):
            for run in list(self._running.values()):
                run.interrupt()
            await self.wait_idle(RELEASE_WAIT_SECONDS)
