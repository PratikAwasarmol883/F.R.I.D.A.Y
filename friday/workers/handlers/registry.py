from friday.workers.handlers.base import TaskHandler


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, handler: TaskHandler) -> None:
        name = handler.task_type.value
        if name in self._handlers:
            raise ValueError(f"handler already registered for {name!r}")
        self._handlers[name] = handler

    def get(self, task_type: str) -> TaskHandler | None:
        return self._handlers.get(task_type)
