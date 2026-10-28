"""Run as a subprocess by tests: claims a task, announces it, then blocks until it is killed."""

import asyncio
import sys

from friday.core.config import settings
from friday.persistence.database import get_sessionmaker
from friday.workers.handlers.registry import HandlerRegistry
from friday.workers.worker import Worker

from .worker_handlers import ScriptedHandler


async def blocked(ctx) -> None:  # type: ignore[no-untyped-def]
    print(f"RUNNING {ctx.task_id} {ctx.attempt}", flush=True)
    await asyncio.sleep(3600)


async def main() -> None:
    handlers = HandlerRegistry()
    handlers.register(ScriptedHandler(blocked))
    worker = Worker(get_sessionmaker(), settings, handlers, worker_id="doomed-worker")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
