"""Bounded model <-> tool loop. Every proposed call goes through policy before it can run."""

import asyncio
import json
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from friday.adapters.llm import (
    AssistantToolTurn,
    ChatMessage,
    LLMMessage,
    LLMProvider,
    LLMResult,
    ToolCall,
    ToolResult,
    ToolResultsTurn,
    ToolSpec,
)
from friday.core.logging_config import get_logger
from friday.core.redaction import hash_arguments, redact_text, redact_value
from friday.domain.enums import MessageRole, ToolStatus
from friday.domain.exceptions import ProviderError, ProviderTimeoutError
from friday.orchestration.policy import (
    Allow,
    Deny,
    DenyReason,
    PolicyContext,
    advertised_tools,
    evaluate,
)
from friday.orchestration.sanitize import wrap_untrusted
from friday.orchestration.tool_contracts import NAME_PATTERN, Source, Tool, ToolError
from friday.orchestration.tool_registry import ToolRegistry

logger = get_logger(__name__)

MAX_RESULT_CHARS = 8000
MAX_STORED_ARGUMENT_CHARS = 2000
MAX_SOURCES = 10

_DENIED_MESSAGE = "Tool call not permitted."
_INVALID_MESSAGE = "Tool call rejected: the arguments did not match the tool's schema."
_FAILED_MESSAGE = (
    "The tool could not complete this request. Continue without it, and tell the user "
    "you were unable to use it."
)


@dataclass(frozen=True)
class ExecutionEntry:
    tool_name: str
    tool_version: str
    risk_class: str | None
    status: ToolStatus
    arguments_redacted: dict[str, Any]
    arguments_hash: str
    result_meta: dict[str, Any] | None
    error_code: str | None
    duration_ms: int


class ExecutionRecorder(Protocol):
    async def record(self, entry: ExecutionEntry) -> uuid.UUID: ...


@dataclass(frozen=True)
class ToolUse:
    name: str
    status: ToolStatus


@dataclass(frozen=True)
class LoopOutcome:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    sources: tuple[Source, ...]
    tools_used: tuple[ToolUse, ...]
    execution_ids: tuple[uuid.UUID, ...]


def safe_tool_name(name: object) -> str:
    """Model-chosen names are untrusted; only well-formed ones are stored or echoed."""
    return name if isinstance(name, str) and NAME_PATTERN.match(name) else "invalid_name"


def _stored_arguments(
    arguments: Any, redacted_fields: frozenset[str], tool: Tool[Any, Any] | None = None
) -> dict[str, Any]:
    value = arguments if isinstance(arguments, dict) else {"value": str(arguments)[:500]}
    if tool is not None and isinstance(arguments, dict):
        value = tool.redact_arguments(dict(arguments))
    redacted = redact_value(value, redacted_fields)
    if len(json.dumps(redacted, default=str)) > MAX_STORED_ARGUMENT_CHARS:
        return {"_truncated": True, "preview": redact_text(json.dumps(value, default=str)[:500])}
    return dict(redacted)


def _hash(arguments: Any) -> str:
    return hash_arguments(arguments if isinstance(arguments, dict) else {"value": str(arguments)})


EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[None]]
UsageCallback = Callable[[LLMResult], Awaitable[None]]


class ToolLoop:
    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        recorder: ExecutionRecorder,
        *,
        system_prompt: str,
        max_output_tokens: int,
        llm_timeout_seconds: float,
        deadline_seconds: float,
        check_budget: Callable[[int], None],
        retry_backoff: tuple[float, float] = (0.1, 0.3),
        on_event: EventCallback | None = None,
        check_cancelled: CancelCheck | None = None,
        on_usage: UsageCallback | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._recorder = recorder
        self._system = system_prompt
        self._max_output_tokens = max_output_tokens
        self._llm_timeout = llm_timeout_seconds
        self._deadline = deadline_seconds
        self._check_budget = check_budget
        self._backoff = retry_backoff
        self._on_event = on_event
        self._check_cancelled = check_cancelled
        self._on_usage = on_usage

    async def _checkpoint(self) -> None:
        if self._check_cancelled is not None:
            await self._check_cancelled()

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            await self._on_event(event_type, payload)

    async def run(
        self, history: Sequence[LLMMessage], user_message: str, ctx: PolicyContext
    ) -> LoopOutcome:
        try:
            async with asyncio.timeout(self._deadline):
                return await self._loop(history, user_message, ctx)
        except TimeoutError as exc:
            raise ProviderTimeoutError from exc

    async def _loop(
        self, history: Sequence[LLMMessage], user_message: str, ctx: PolicyContext
    ) -> LoopOutcome:
        specs = [
            ToolSpec(
                t.contract.name, t.contract.description, t.contract.input_model.model_json_schema()
            )
            for t in advertised_tools(self._registry, ctx)
        ]
        messages: list[LLMMessage] = [*history, ChatMessage(MessageRole.USER, user_message)]
        tokens_in = tokens_out = 0
        sources: list[Source] = []
        uses: list[ToolUse] = []
        execution_ids: list[uuid.UUID] = []
        max_calls = ctx.max_total_calls_per_ask if specs else 0

        for iteration in range(max_calls + 1):
            await self._checkpoint()
            self._check_budget(tokens_in + tokens_out)
            final_turn = iteration == max_calls
            await self._emit("model_call", {"call": iteration + 1})
            result = await self._llm.complete(
                system=self._system,
                messages=messages,
                max_output_tokens=self._max_output_tokens,
                timeout_seconds=self._llm_timeout,
                tools=specs,
                tool_choice="none" if (final_turn and specs) else "auto",
            )
            if self._on_usage is not None:
                await self._on_usage(result)  # spend is recorded even if the ask later fails
            tokens_in += result.input_tokens
            tokens_out += result.output_tokens

            if not result.tool_calls:
                if not result.text.strip():
                    raise ProviderError("Empty final answer")
                return LoopOutcome(
                    text=result.text,
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    model=result.model,
                    provider=result.provider,
                    sources=tuple(sources[:MAX_SOURCES]),
                    tools_used=tuple(uses),
                    execution_ids=tuple(execution_ids),
                )
            if final_turn:
                raise ProviderError("Tool call requested when tools were disabled")

            results = []
            for call in result.tool_calls:
                tool_result, use, found, execution_id = await self._handle_call(call, ctx)
                results.append(tool_result)
                uses.append(use)
                await self._emit("tool_call", {"tool": use.name, "status": use.status.value})
                execution_ids.append(execution_id)
                for source in found:
                    if all(source.url != existing.url for existing in sources):
                        sources.append(source)
            messages.append(AssistantToolTurn(result.text, result.tool_calls))
            messages.append(ToolResultsTurn(tuple(results)))

        raise ProviderError("Tool loop ended without an answer")  # unreachable by construction

    async def _handle_call(
        self, call: ToolCall, ctx: PolicyContext
    ) -> tuple[ToolResult, ToolUse, tuple[Source, ...], uuid.UUID]:
        await self._checkpoint()  # never start a tool call after cancellation
        name = safe_tool_name(call.name)
        decision = evaluate(self._registry, call.name, call.arguments, ctx)
        if isinstance(decision, Deny):
            return await self._denied(call, name, decision)
        return await self._execute_allowed(call, name, decision, ctx)

    async def _denied(
        self, call: ToolCall, name: str, decision: Deny
    ) -> tuple[ToolResult, ToolUse, tuple[Source, ...], uuid.UUID]:
        tool = self._registry.get(call.name)
        entry = ExecutionEntry(
            tool_name=name,
            tool_version=tool.contract.version if tool else "-",
            risk_class=str(tool.contract.risk_class) if tool else None,
            status=ToolStatus.DENIED,
            arguments_redacted=_stored_arguments(
                call.arguments, tool.contract.redacted_fields if tool else frozenset(), tool
            ),
            arguments_hash=_hash(call.arguments),
            result_meta=None,
            error_code=decision.reason.value,
            duration_ms=0,
        )
        execution_id = await self._recorder.record(entry)
        message = (
            _INVALID_MESSAGE if decision.reason is DenyReason.INVALID_ARGUMENTS else _DENIED_MESSAGE
        )
        return (
            ToolResult(call.id, message, is_error=True),
            ToolUse(name, ToolStatus.DENIED),
            (),
            execution_id,
        )

    async def _execute_allowed(
        self, call: ToolCall, name: str, decision: Allow, ctx: PolicyContext
    ) -> tuple[ToolResult, ToolUse, tuple[Source, ...], uuid.UUID]:
        tool, contract = decision.tool, decision.tool.contract
        ctx.record_call(contract.name)
        started = time.perf_counter()
        status, error_code, content, is_error = ToolStatus.SUCCEEDED, None, "", False
        meta: dict[str, Any] | None = None
        sources: tuple[Source, ...] = ()
        try:
            output = await self._run_with_limits(tool, decision.arguments)
            summary = tool.summarize(output)
            meta, sources = summary.meta, summary.sources
            content = output.model_dump_json()[:MAX_RESULT_CHARS]
            if contract.returns_untrusted_content:
                content = wrap_untrusted(content)
        except TimeoutError:
            status, error_code, content, is_error = (
                ToolStatus.TIMED_OUT,
                "timeout",
                _FAILED_MESSAGE,
                True,
            )
        except ToolError as exc:
            status, error_code, content, is_error = (
                ToolStatus.FAILED,
                exc.code,
                _FAILED_MESSAGE,
                True,
            )
        except Exception:
            logger.exception("Tool crashed")
            status, error_code, content, is_error = (
                ToolStatus.FAILED,
                "internal_error",
                _FAILED_MESSAGE,
                True,
            )

        entry = ExecutionEntry(
            tool_name=contract.name,
            tool_version=contract.version,
            risk_class=str(contract.risk_class),
            status=status,
            arguments_redacted=_stored_arguments(call.arguments, contract.redacted_fields, tool),
            arguments_hash=_hash(call.arguments),
            result_meta=meta,
            error_code=error_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        execution_id = await self._recorder.record(entry)
        return ToolResult(call.id, content, is_error), ToolUse(name, status), sources, execution_id

    async def _run_with_limits(self, tool: Tool[Any, Any], arguments: Any) -> Any:
        """One timeout covers all attempts; only retryable errors on read-only tools retry."""
        contract = tool.contract
        attempts = contract.max_retries + 1
        async with asyncio.timeout(contract.timeout_seconds):
            for attempt in range(attempts):
                try:
                    return await tool.run(arguments)
                except ToolError as exc:
                    if not exc.retryable or attempt == attempts - 1:
                        raise
                    await asyncio.sleep(random.uniform(*self._backoff))
        raise AssertionError("unreachable")  # pragma: no cover
