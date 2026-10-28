"""Helpers for driving real worker loops in tests."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from friday.core.config import Settings
from friday.workers.worker import Worker

from .conftest import SessionFactory
from .task_helpers import sql, tx

FAST: dict[str, Any] = {
    "task_poll_interval_seconds": 0.02,
    "task_heartbeat_seconds": 0.05,
    "task_lease_seconds": 0.5,
    "task_retry_base_seconds": 0.02,
    "task_retry_max_seconds": 0.05,
    "task_shutdown_grace_seconds": 2.0,
}
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled"}


def settings_for(**overrides: Any) -> Settings:
    return Settings(_env_file=None).model_copy(update={**FAST, **overrides})


async def wait_until(predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not reached in time")
        await asyncio.sleep(0.02)


def drive(
    factory: SessionFactory,
    handlers: Any,
    scenario: Callable[[list[Worker]], Awaitable[None]],
    *,
    settings: Settings | None = None,
    workers: int = 1,
) -> list[Worker]:
    """Run worker loop(s) while `scenario` observes and pokes at the system, then stop them."""

    async def main() -> list[Worker]:
        cfg = settings or settings_for()
        pool = [Worker(factory, cfg, handlers, worker_id=f"w{i}") for i in range(workers)]
        loops = [asyncio.create_task(w.run()) for w in pool]
        try:
            await scenario(pool)
        finally:
            for w in pool:
                w.request_stop()
            await asyncio.gather(*loops)
        return pool

    return asyncio.run(main())


def state(db_url: str, task_id: Any) -> tuple[str, int, str | None]:
    return sql(db_url, "SELECT status, attempt, error_code FROM tasks WHERE id = %s", (task_id,))[0]


def event_types(factory: SessionFactory, task_id: Any) -> list[str]:
    return [e.event_type for e in tx(factory, lambda r: r.events_after(task_id, 0, 100))]
