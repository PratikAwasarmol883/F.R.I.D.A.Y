from pydantic import BaseModel, ConfigDict, Field

from friday.domain.enums import RiskClass
from friday.orchestration.tool_contracts import Tool, ToolContract


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=50)
    times: int = Field(default=1, ge=1, le=3)


class EchoOutput(BaseModel):
    text: str


class EchoTool(Tool[EchoInput, EchoOutput]):
    """Read-only test tool; rejects the word 'forbidden' via its guard."""

    def __init__(self, **overrides: object) -> None:
        fields: dict[str, object] = {
            "name": "echo",
            "version": "1",
            "description": "Echo text back.",
            "input_model": EchoInput,
            "output_model": EchoOutput,
            "risk_class": RiskClass.READ_ONLY,
            "required_scope": "tool:echo",
            "timeout_seconds": 1.0,
        }
        fields.update(overrides)
        self.contract = ToolContract(**fields)  # type: ignore[arg-type]
        self.calls = 0

    def guard(self, arguments: EchoInput) -> str | None:
        return "forbidden_word" if "forbidden" in arguments.text else None

    async def run(self, arguments: EchoInput) -> EchoOutput:
        self.calls += 1
        return EchoOutput(text=arguments.text * arguments.times)


def danger_tool(name: str = "send_email", **overrides: object) -> EchoTool:
    fields: dict[str, object] = {
        "name": name,
        "required_scope": f"tool:{name}",
        "risk_class": RiskClass.CONSEQUENTIAL,
        "requires_approval": True,
    }
    fields.update(overrides)
    return EchoTool(**fields)
