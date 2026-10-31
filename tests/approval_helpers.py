"""Builders and shortcuts for approval and integration tests."""

from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from friday.adapters.llm.fake import FakeLLMProvider
from friday.adapters.notify.fake import FakeNotifier
from friday.domain.enums import DeviceType
from friday.services.auth_service import AuthService

from .conftest import Seeded, SessionFactory, run_db
from .tool_helpers import final, tool_turn

KEY = Fernet.generate_key().decode()
TOPIC = "alice-private-topic-1234"
SETTINGS: dict[str, Any] = {"integration_key": SecretStr(KEY)}
DECIDER = ["approvals:decide", "integrations:manage", "tool:send_notification"]

ASK = "/api/v1/assistant/ask"
APPROVALS = "/api/v1/approvals"
INTEGRATIONS = "/api/v1/integrations"


def add_device(factory: SessionFactory, who: Seeded, name: str, scopes: list[str]) -> Seeded:
    async def go(session: AsyncSession) -> Seeded:
        device, token = await AuthService(session).register_device(
            who.user_id, name, DeviceType.CLI, scopes
        )
        return Seeded(who.user_id, device.id, token)

    return run_db(factory, go)


def connect(client: TestClient, who: Seeded, topic: str = TOPIC) -> Any:
    return client.post(f"{INTEGRATIONS}/ntfy/connect", json={"topic": topic}, headers=who.headers)


def notify_call(title: str = "Standup", message: str = "Standup starts in 5 minutes") -> Any:
    return tool_turn(("send_notification", {"title": title, "message": message}))


def propose(
    make_client: Any,
    who: Seeded,
    notifier: FakeNotifier | None = None,
    *,
    title: str = "Standup",
    message: str = "Standup starts in 5 minutes",
    **overrides: Any,
) -> tuple[TestClient, FakeNotifier, dict[str, Any]]:
    """Connect the integration, let the model propose one notification, return the approval."""
    notifier = notifier or FakeNotifier()
    llm = FakeLLMProvider(script=[notify_call(title, message), final("I asked for approval.")])
    client = make_client(llm, notifier=notifier, **{**SETTINGS, **overrides})
    assert connect(client, who).status_code == 201
    assert client.post(ASK, json={"message": "remind me"}, headers=who.headers).status_code == 200
    approval = client.get(APPROVALS, headers=who.headers).json()[0]  # newest first
    return client, notifier, approval


def decide(
    client: TestClient,
    who: Seeded,
    approval: dict[str, Any],
    decision: str = "approve",
    **extra: Any,
) -> Any:
    body = {"decision": decision, "arguments_hash": approval["arguments_hash"], **extra}
    return client.post(f"{APPROVALS}/{approval['id']}/decision", json=body, headers=who.headers)
