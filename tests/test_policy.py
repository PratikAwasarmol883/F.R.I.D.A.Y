from typing import Any

import pytest

from friday.orchestration.policy import (
    Allow,
    Deny,
    DenyReason,
    PolicyContext,
    advertised_tools,
    evaluate,
)
from friday.orchestration.tool_registry import ToolRegistry

from .tool_fixtures import EchoTool, danger_tool


def _ctx(**overrides: Any) -> PolicyContext:
    fields: dict[str, Any] = {
        "enabled_tools": frozenset({"echo", "send_email"}),
        "available_tools": frozenset({"echo", "send_email"}),
        "device_scopes": frozenset({"tool:echo", "tool:send_email"}),
        "max_total_calls_per_ask": 3,
    }
    fields.update(overrides)
    return PolicyContext(**fields)


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool(daily_limit=2, max_calls_per_ask=2))
    reg.register(danger_tool())
    return reg


def _reason(decision: Any) -> DenyReason:
    assert isinstance(decision, Deny), decision
    return decision.reason


def test_valid_call_is_allowed_with_validated_arguments(registry: ToolRegistry) -> None:
    decision = evaluate(registry, "echo", {"text": "hi"}, _ctx())
    assert isinstance(decision, Allow)
    assert decision.arguments.model_dump() == {"text": "hi", "times": 1}


def test_unknown_tool_is_denied(registry: ToolRegistry) -> None:
    assert _reason(evaluate(registry, "rm_rf", {}, _ctx())) is DenyReason.UNKNOWN_TOOL


def test_disabled_or_unconfigured_tool_is_denied(registry: ToolRegistry) -> None:
    disabled = _ctx(enabled_tools=frozenset())
    assert _reason(evaluate(registry, "echo", {"text": "x"}, disabled)) is DenyReason.TOOL_DISABLED
    unconfigured = _ctx(available_tools=frozenset())
    assert (
        _reason(evaluate(registry, "echo", {"text": "x"}, unconfigured)) is DenyReason.TOOL_DISABLED
    )


def test_missing_scope_is_denied(registry: ToolRegistry) -> None:
    ctx = _ctx(device_scopes=frozenset())
    assert _reason(evaluate(registry, "echo", {"text": "x"}, ctx)) is DenyReason.MISSING_SCOPE


def test_consequential_tools_are_denied_even_when_enabled_and_scoped(
    registry: ToolRegistry,
) -> None:
    decision = evaluate(registry, "send_email", {"text": "x"}, _ctx())
    assert _reason(decision) is DenyReason.APPROVAL_REQUIRED


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"text": ""},
        {"text": "x" * 51},
        {"text": "x", "extra": 1},
        {"text": 5},
        {"text": "x", "times": "2"},
        {"text": "x", "times": 4},
        ["text"],
        "text",
        None,
    ],
)
def test_invalid_arguments_are_denied(registry: ToolRegistry, arguments: Any) -> None:
    assert _reason(evaluate(registry, "echo", arguments, _ctx())) is DenyReason.INVALID_ARGUMENTS


def test_tool_guard_can_reject_valid_arguments(registry: ToolRegistry) -> None:
    decision = evaluate(registry, "echo", {"text": "a forbidden thing"}, _ctx())
    assert _reason(decision) is DenyReason.GUARD_REJECTED
    assert isinstance(decision, Deny) and decision.detail == "forbidden_word"


def test_per_tool_and_total_call_caps(registry: ToolRegistry) -> None:
    ctx = _ctx()
    for _ in range(2):
        assert isinstance(evaluate(registry, "echo", {"text": "x"}, ctx), Allow)
        ctx.record_call("echo")
    assert _reason(evaluate(registry, "echo", {"text": "x"}, ctx)) is DenyReason.CALL_LIMIT

    total = _ctx(max_total_calls_per_ask=1)
    total.record_call("other")
    assert _reason(evaluate(registry, "echo", {"text": "x"}, total)) is DenyReason.CALL_LIMIT


def test_daily_limit(registry: ToolRegistry) -> None:
    ctx = _ctx(daily_counts={"echo": 2})
    assert _reason(evaluate(registry, "echo", {"text": "x"}, ctx)) is DenyReason.DAILY_LIMIT


def test_checks_run_in_order_so_denials_leak_nothing_about_later_checks(
    registry: ToolRegistry,
) -> None:
    ctx = _ctx(device_scopes=frozenset())
    bad_args = evaluate(registry, "echo", {"bogus": 1}, ctx)
    assert _reason(bad_args) is DenyReason.MISSING_SCOPE  # scope before argument validation


def test_only_permitted_read_only_tools_are_advertised(registry: ToolRegistry) -> None:
    assert [t.contract.name for t in advertised_tools(registry, _ctx())] == ["echo"]
    assert advertised_tools(registry, _ctx(device_scopes=frozenset())) == []
    assert advertised_tools(registry, _ctx(enabled_tools=frozenset())) == []
