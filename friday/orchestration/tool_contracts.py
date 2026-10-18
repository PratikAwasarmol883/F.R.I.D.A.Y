"""Declarative contracts every tool must satisfy before it can be registered."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from friday.domain.enums import RiskClass

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
SCOPE_PATTERN = re.compile(r"^tool:[a-z][a-z0-9_]{2,40}$")
MAX_TIMEOUT_SECONDS = 60.0

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ToolRegistrationError(ValueError):
    """A tool violates a registration invariant."""


class ToolError(Exception):
    """A tool failed. `code` is stored for audit; the model only sees a generic message."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class Source:
    title: str
    url: str


@dataclass(frozen=True)
class ToolSummary:
    """What may be persisted or shown to clients about a result; never the full body."""

    meta: dict[str, Any] = field(default_factory=dict)
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True)
class ToolContract:
    name: str
    version: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk_class: RiskClass
    required_scope: str
    timeout_seconds: float
    max_retries: int = 0
    requires_approval: bool = False
    returns_untrusted_content: bool = False
    redacted_fields: frozenset[str] = frozenset()
    max_calls_per_ask: int = 3
    daily_limit: int | None = None

    def validate(self) -> None:
        problems: list[str] = []
        if not NAME_PATTERN.match(self.name):
            problems.append("invalid name")
        if not SCOPE_PATTERN.match(self.required_scope):
            problems.append("invalid required_scope")
        if not self.version.strip() or not self.description.strip():
            problems.append("version and description are required")
        if not 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            problems.append("timeout_seconds out of range")
        if self.max_retries < 0 or (
            self.max_retries > 0 and self.risk_class != RiskClass.READ_ONLY
        ):
            problems.append("retries are only allowed for read-only tools")
        if (
            self.risk_class in {RiskClass.CONSEQUENTIAL, RiskClass.DESTRUCTIVE}
            and not self.requires_approval
        ):
            problems.append("consequential/destructive tools must require approval")
        if self.input_model.model_config.get("extra") != "forbid":
            problems.append("input model must forbid extra fields")
        if self.max_calls_per_ask < 1 or (self.daily_limit is not None and self.daily_limit < 1):
            problems.append("call limits must be positive")
        if problems:
            raise ToolRegistrationError(f"{self.name!r}: " + "; ".join(problems))


class Tool(ABC, Generic[InputT, OutputT]):
    """One attempt at the operation; timeouts, retries, and policy live in the executor."""

    contract: ToolContract

    def guard(self, arguments: InputT) -> str | None:
        """Tool-specific semantic check run by policy; return a reason code to reject."""
        return None

    def summarize(self, output: OutputT) -> ToolSummary:
        """Metadata for audit rows and client-visible sources; default exposes nothing."""
        return ToolSummary()

    @abstractmethod
    async def run(self, arguments: InputT) -> OutputT: ...
