"""Entry point: `python -m friday.worker`."""

import asyncio
import signal

from friday.composition import build_llm, get_fetcher, get_registry, get_search_provider
from friday.core.config import settings
from friday.core.logging_config import get_logger, setup_logging
from friday.persistence.database import get_engine, get_sessionmaker
from friday.workers.handlers.assistant_ask import AssistantAskHandler
from friday.workers.handlers.registry import HandlerRegistry
from friday.workers.handlers.research import ResearchHandler
from friday.workers.worker import Worker

logger = get_logger(__name__)


def build_worker() -> Worker:
    session_factory = get_sessionmaker()
    handlers = HandlerRegistry()
    handlers.register(
        AssistantAskHandler(
            session_factory,
            settings,
            llm_factory=lambda: build_llm(settings),
            registry_factory=lambda: get_registry(settings),
        )
    )
    handlers.register(
        ResearchHandler(
            session_factory,
            settings,
            llm_factory=lambda: build_llm(settings),
            search_factory=lambda: get_search_provider(settings),
            fetcher_factory=get_fetcher,
        )
    )
    return Worker(session_factory, settings, handlers)


async def amain() -> None:
    setup_logging()
    worker = build_worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)
    try:
        await worker.run()
    finally:
        await get_engine().dispose()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
