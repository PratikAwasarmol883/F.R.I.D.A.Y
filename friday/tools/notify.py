"""The `send_notification` tool. The assistant can only propose it; the user approves each send."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from friday.core.redaction import contains_secret
from friday.domain.enums import RiskClass
from friday.orchestration.tool_contracts import Tool, ToolContract, ToolSummary

TOOL_NAME = "send_notification"
PROVIDER = "ntfy"
MAX_TITLE = 80
MAX_MESSAGE = 500


class NotifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE, description="Short plain-text title.")
    message: str = Field(min_length=1, max_length=MAX_MESSAGE, description="Plain-text message.")


class NotifyOutput(BaseModel):
    status: str


def _clean(text: str, *, allow_newlines: bool) -> bool:
    return not any(
        ord(ch) < 32 and not (allow_newlines and ch == "\n") or ord(ch) == 127 for ch in text
    )


class SendNotificationTool(Tool[NotifyInput, NotifyOutput]):
    def __init__(self, sender: Callable[[str, str], Awaitable[None]], daily_limit: int) -> None:
        self._sender = sender
        self.contract = ToolContract(
            name=TOOL_NAME,
            version="1",
            description=(
                "Propose a push notification to the user's own phone. It is only sent if the "
                "user approves it. Plain text only; never include credentials."
            ),
            input_model=NotifyInput,
            output_model=NotifyOutput,
            risk_class=RiskClass.CONSEQUENTIAL,
            required_scope=f"tool:{TOOL_NAME}",
            timeout_seconds=15.0,
            requires_approval=True,
            max_calls_per_ask=1,
            daily_limit=daily_limit,
        )

    def guard(self, arguments: NotifyInput) -> str | None:
        if not _clean(arguments.title, allow_newlines=False) or not _clean(
            arguments.message, allow_newlines=True
        ):
            return "control_characters"
        if contains_secret(arguments.title) or contains_secret(arguments.message):
            return "credential"
        return None

    def describe_action(self, arguments: NotifyInput) -> str:
        return f'Send a push notification titled "{arguments.title}": {arguments.message}'

    def redact_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            key: f"[{len(value)} chars]" if isinstance(value, str) else "[redacted]"
            for key, value in arguments.items()
        }  # the full text lives only in the approval, which the user reviews

    async def run(self, arguments: NotifyInput) -> NotifyOutput:
        await self._sender(arguments.title, arguments.message)
        return NotifyOutput(status="sent")

    def summarize(self, output: NotifyOutput) -> ToolSummary:
        return ToolSummary(meta={"status": output.status})
