from typing import Protocol


class NotifyError(Exception):
    """Delivery failed. `code` is stable and safe to store; provider details are never kept."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Notifier(Protocol):
    async def send(self, topic: str, title: str, message: str, timeout_seconds: float) -> None: ...
