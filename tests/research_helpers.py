"""Fakes and builders for research tests."""

import asyncio
import json
from typing import Any

from friday.adapters.fetch.fake import FakeFetcher
from friday.adapters.llm.fake import FakeLLMProvider
from friday.adapters.search.base import SearchHit
from friday.adapters.search.fake import FakeSearchProvider
from friday.core.config import Settings
from friday.workers.context import TaskContext
from friday.workers.handlers.research import ResearchHandler

from .conftest import Seeded, SessionFactory
from .task_helpers import claim_one, new_task
from .tool_helpers import final, tool_turn

A_URL = "https://a.example/page"
B_URL = "https://b.example/page"
HITS = [SearchHit("Alpha", A_URL, "alpha snippet"), SearchHit("Beta", B_URL, "beta snippet")]
PAGE = (
    "<html><head><title>Alpha Page</title></head><body><h1>Alpha</h1>"
    "<p>The answer is forty-two according to Alpha.</p></body></html>"
)


def report_json(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "summary": "The answer is forty-two.",
        "findings": [{"claim": "It is forty-two", "source_ids": ["S1"], "confidence": "supported"}],
        "conflicts": [],
        "limitations": ["Only one source was read."],
    }
    data.update(overrides)
    return json.dumps(data)


def script(*, fetch: tuple[str, ...] = (A_URL,), report: str | None = None) -> list[Any]:
    """Search, fetch the given URLs, then return the final report."""
    turns: list[Any] = [tool_turn(("web_search", {"query": "forty two"}))]
    if fetch:
        turns.append(tool_turn(*[("web_fetch", {"url": url}) for url in fetch]))
    turns.append(final(report if report is not None else report_json()))
    return turns


class Env:
    def __init__(self, llm: FakeLLMProvider | None = None) -> None:
        self.llm = llm or FakeLLMProvider(script=script())
        self.search = FakeSearchProvider(hits=list(HITS))
        self.fetcher = FakeFetcher()
        self.fetcher.add_html(A_URL, PAGE)
        self.fetcher.add_html(B_URL, PAGE.replace("Alpha", "Beta"))

    def handler(self, factory: Any, settings: Any) -> ResearchHandler:
        return ResearchHandler(
            factory,
            settings,
            llm_factory=lambda: self.llm,
            search_factory=lambda: self.search,
            fetcher_factory=lambda: self.fetcher,
        )


def make_task(
    factory: SessionFactory, who: Seeded, question: str = "What is the answer?", **extra: Any
):  # type: ignore[no-untyped-def]
    return new_task(
        factory,
        who,
        task_type="research.run",
        task_input={"question": question, **extra},
    )


def run(
    factory: SessionFactory,
    task: Any,
    env: Env,
    *,
    worker: str = "w1",
    attempt: int | None = None,
    **settings: Any,
) -> None:
    config = Settings(_env_file=None).model_copy(update=settings)
    ctx = TaskContext(
        task_id=task.id,
        user_id=task.user_id,
        device_id=task.device_id,
        attempt=attempt if attempt is not None else task.attempt,
        worker_id=worker,
        task_input=task.input,
        session_factory=factory,
    )
    asyncio.run(env.handler(factory, config).run(ctx))


def claimed(factory: SessionFactory, who: Seeded, **kwargs: Any):  # type: ignore[no-untyped-def]
    make_task(factory, who, **kwargs)
    return claim_one(factory)


def produce_report(
    factory: SessionFactory, who: Seeded, env: "Env | None" = None
) -> tuple[str, str]:
    """Run a research task to completion at handler level; returns (task_id, report_id)."""
    task = claimed(factory, who)
    run(factory, task, env or Env())
    from .task_helpers import fetch

    done = fetch(factory, task.id)
    assert done.result is not None
    return str(task.id), done.result["report_id"]
