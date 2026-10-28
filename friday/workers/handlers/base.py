from typing import Protocol

from friday.domain.task_state import TaskType
from friday.workers.context import TaskContext


class TaskHandler(Protocol):
    """Runs one attempt. A handler that produces user-visible state must commit it in the same
    transaction as `ctx.mark_succeeded`, so a stale worker can never leave duplicate effects."""

    task_type: TaskType

    async def run(self, ctx: TaskContext) -> None: ...
