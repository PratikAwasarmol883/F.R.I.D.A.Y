from dataclasses import dataclass, field

from friday.adapters.notify.base import NotifyError


@dataclass
class SentNotification:
    topic: str
    title: str
    message: str


@dataclass
class FakeNotifier:
    fail_with: str | None = None
    sent: list[SentNotification] = field(default_factory=list)

    async def send(self, topic: str, title: str, message: str, timeout_seconds: float) -> None:
        if self.fail_with is not None:
            raise NotifyError(self.fail_with)
        self.sent.append(SentNotification(topic, title, message))
