"""The `remember` tool: the assistant can only *suggest* notes; the user approves each one."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from friday.domain.enums import RiskClass
from friday.domain.exceptions import MemoryLimitError, MemoryRejectedError
from friday.orchestration.memory import validate_memory_value
from friday.orchestration.tool_contracts import Tool, ToolContract, ToolError, ToolSummary


class RememberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["preference", "fact", "project", "note"]
    value: str = Field(
        min_length=1, max_length=300, description="One short plain-text note about the user."
    )


class RememberOutput(BaseModel):
    status: str
    note: str


class RememberTool(Tool[RememberInput, RememberOutput]):
    def __init__(self, writer: Callable[[str, str], Awaitable[None]]) -> None:
        self._writer = writer
        self.contract = ToolContract(
            name="remember",
            version="1",
            description=(
                "Suggest saving one short note about the user (a preference, fact, project, or "
                "note). The note is only a suggestion until the user approves it. Never include "
                "credentials or links."
            ),
            input_model=RememberInput,
            output_model=RememberOutput,
            risk_class=RiskClass.REVERSIBLE_WRITE,
            required_scope="tool:remember",
            timeout_seconds=5.0,
            max_retries=0,
            max_calls_per_ask=2,
            daily_limit=20,
        )

    def guard(self, arguments: RememberInput) -> str | None:
        try:
            validate_memory_value(arguments.value, allow_urls=False, max_chars=300)
        except MemoryRejectedError as exc:
            return exc.reason
        return None

    def redact_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        value = arguments.get("value")
        masked = f"[{len(value)} chars]" if isinstance(value, str) else "[redacted]"
        return {**arguments, "value": masked}  # note text is user data, never stored in audit

    async def run(self, arguments: RememberInput) -> RememberOutput:
        try:
            await self._writer(arguments.kind, arguments.value)
        except MemoryLimitError as exc:
            raise ToolError("memory_limit") from exc
        except MemoryRejectedError as exc:
            raise ToolError(exc.reason) from exc
        return RememberOutput(
            status="pending_user_approval",
            note="Saved as a suggestion. It is not remembered until the user approves it.",
        )

    def summarize(self, output: RememberOutput) -> ToolSummary:
        return ToolSummary(meta={"status": output.status})
