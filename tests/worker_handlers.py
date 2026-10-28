"""Scripted task handlers for worker tests (never registered in production)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.domain.task_state import TaskType
from friday.workers.context import TaskContext
from friday.workers.handlers.registry import HandlerRegistry

Behavior = Callable[[TaskContext], Awaitable[None]]


class ScriptedHandler:
    task_type = TaskType.ASSISTANT_ASK

    def __init__(self, behavior: Behavior) -> None:
        self.behavior = behavior
        self.runs: list[tuple[uuid.UUID, int]] = []  # (task id, attempt)

    async def run(self, ctx: TaskContext) -> None:
        self.runs.append((ctx.task_id, ctx.attempt))
        await self.behavior(ctx)


def registry_for(handler: Any) -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(handler)
    return registry


async def succeed(
    ctx: TaskContext,
    factory: async_sessionmaker[AsyncSession],
    result: dict[str, Any] | None = None,
    hold_seconds: float = 0.0,
) -> None:
    """Finish the way a real handler must: lock, write, and mark success in one transaction."""
    async with factory() as session:
        await ctx.lock_for_finish(session)
        if hold_seconds:
            await asyncio.sleep(hold_seconds)
        await ctx.mark_succeeded(session, result or {"ok": True})
        await session.commit()
