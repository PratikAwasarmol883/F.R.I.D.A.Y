"""Deterministic, fail-closed authorization of model-proposed tool calls. No I/O."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from friday.domain.enums import RiskClass
from friday.orchestration.tool_contracts import Tool
from friday.orchestration.tool_registry import ToolRegistry

_NEEDS_APPROVAL = frozenset({RiskClass.CONSEQUENTIAL, RiskClass.DESTRUCTIVE})


class DenyReason(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_DISABLED = "tool_disabled"
    MISSING_SCOPE = "missing_scope"
    APPROVAL_REQUIRED = "approval_required"
    INVALID_ARGUMENTS = "invalid_arguments"
    GUARD_REJECTED = "guard_rejected"
    CALL_LIMIT = "call_limit"
    DAILY_LIMIT = "daily_limit"


@dataclass(frozen=True)
class Allow:
    tool: Tool[Any, Any]
    arguments: BaseModel


@dataclass(frozen=True)
class Deny:
    reason: DenyReason
    detail: str | None = None


Decision = Allow | Deny


@dataclass
class PolicyContext:
    enabled_tools: frozenset[str]
    available_tools: frozenset[str]
    device_scopes: frozenset[str]
    max_total_calls_per_ask: int
    daily_counts: dict[str, int] = field(default_factory=dict)
    calls_per_tool: dict[str, int] = field(default_factory=dict)
    total_calls: int = 0

    def record_call(self, tool_name: str) -> None:
        self.total_calls += 1
        self.calls_per_tool[tool_name] = self.calls_per_tool.get(tool_name, 0) + 1
        self.daily_counts[tool_name] = self.daily_counts.get(tool_name, 0) + 1


def _statically_permitted(tool: Tool[Any, Any], ctx: PolicyContext) -> DenyReason | None:
    contract = tool.contract
    if contract.name not in ctx.enabled_tools or contract.name not in ctx.available_tools:
        return DenyReason.TOOL_DISABLED
    if contract.required_scope not in ctx.device_scopes:
        return DenyReason.MISSING_SCOPE
    if contract.requires_approval or contract.risk_class in _NEEDS_APPROVAL:
        return DenyReason.APPROVAL_REQUIRED  # consequential work waits for the approval flow
    return None


def advertised_tools(registry: ToolRegistry, ctx: PolicyContext) -> list[Tool[Any, Any]]:
    """Only tools that would pass the static checks are ever shown to the model."""
    return [tool for tool in registry.all() if _statically_permitted(tool, ctx) is None]


def evaluate(
    registry: ToolRegistry, tool_name: str, arguments: Mapping[str, Any] | Any, ctx: PolicyContext
) -> Decision:
    tool = registry.get(tool_name)
    if tool is None:
        return Deny(DenyReason.UNKNOWN_TOOL)
    if (reason := _statically_permitted(tool, ctx)) is not None:
        return Deny(reason)

    contract = tool.contract
    if not isinstance(arguments, dict):
        return Deny(DenyReason.INVALID_ARGUMENTS)
    try:
        validated = contract.input_model.model_validate(arguments, strict=True)
    except ValidationError:
        return Deny(DenyReason.INVALID_ARGUMENTS)

    if (guard_reason := tool.guard(validated)) is not None:
        return Deny(DenyReason.GUARD_REJECTED, guard_reason)

    if (
        ctx.total_calls >= ctx.max_total_calls_per_ask
        or ctx.calls_per_tool.get(contract.name, 0) >= contract.max_calls_per_ask
    ):
        return Deny(DenyReason.CALL_LIMIT)
    if (
        contract.daily_limit is not None
        and ctx.daily_counts.get(contract.name, 0) >= contract.daily_limit
    ):
        return Deny(DenyReason.DAILY_LIMIT)
    return Allow(tool, validated)
