import pytest
from pydantic import BaseModel

from friday.domain.enums import RiskClass
from friday.orchestration.tool_contracts import ToolRegistrationError
from friday.orchestration.tool_registry import ToolRegistry

from .tool_fixtures import EchoTool, danger_tool


def test_valid_tool_registers_and_is_retrievable() -> None:
    registry = ToolRegistry()
    tool = EchoTool()
    registry.register(tool)
    assert registry.get("echo") is tool
    assert registry.get("missing") is None


def test_duplicate_names_are_rejected() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ToolRegistrationError, match="already registered"):
        registry.register(EchoTool())


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "Bad Name"},
        {"name": "ab"},
        {"required_scope": "web_search"},
        {"required_scope": "tool:"},
        {"version": " "},
        {"description": ""},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"max_retries": -1},
        {"max_calls_per_ask": 0},
        {"daily_limit": 0},
    ],
)
def test_invalid_contracts_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry().register(EchoTool(**overrides))


def test_input_model_must_forbid_extra_fields() -> None:
    class Loose(BaseModel):
        text: str = "x"

    with pytest.raises(ToolRegistrationError, match="forbid extra"):
        ToolRegistry().register(EchoTool(input_model=Loose))


@pytest.mark.parametrize("risk", [RiskClass.CONSEQUENTIAL, RiskClass.DESTRUCTIVE])
def test_dangerous_tools_must_require_approval(risk: RiskClass) -> None:
    with pytest.raises(ToolRegistrationError, match="require approval"):
        ToolRegistry().register(danger_tool(risk_class=risk, requires_approval=False))


def test_retries_only_allowed_for_read_only_tools() -> None:
    with pytest.raises(ToolRegistrationError, match="read-only"):
        ToolRegistry().register(danger_tool(max_retries=1))
    ToolRegistry().register(EchoTool(max_retries=1))
