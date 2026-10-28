"""Task lifecycle rules. Pure: no I/O, no framework imports."""

import random
from enum import StrEnum

from friday.domain.exceptions import (
    BudgetExceededError,
    DeviceRevokedError,
    ForbiddenError,
    InvalidTaskInputError,
    NotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class TaskType(StrEnum):
    ASSISTANT_ASK = "assistant.ask"
    RESEARCH = "research.run"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.CANCELLED}
)
ACTIVE_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED})
LEASED_STATUSES = frozenset({TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED})

_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.CANCELLED,  # only the lease holder, e.g. creating device was revoked
            TaskStatus.QUEUED,  # retry, lease expiry, or graceful release
        }
    ),
    TaskStatus.CANCEL_REQUESTED: frozenset({TaskStatus.CANCELLED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.TIMED_OUT: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}
_OPERATOR_EXITS = frozenset({TaskStatus.FAILED, TaskStatus.TIMED_OUT})


class InvalidTransitionError(Exception):
    """A writer attempted a transition the state machine forbids; always a bug."""


def can_transition(current: TaskStatus, target: TaskStatus, *, operator: bool = False) -> bool:
    if operator and current in _OPERATOR_EXITS and target is TaskStatus.QUEUED:
        return True
    return target in _TRANSITIONS[current]


def assert_transition(current: TaskStatus, target: TaskStatus, *, operator: bool = False) -> None:
    if not can_transition(current, target, operator=operator):
        raise InvalidTransitionError(f"{current.value} -> {target.value}")


class TaskEventType(StrEnum):
    CREATED = "created"
    CLAIMED = "claimed"
    PROGRESS = "progress"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCEL_REQUESTED = "cancel_requested"
    LEASE_EXPIRED_REQUEUED = "lease_expired_requeued"
    LEASE_LOST = "lease_lost"
    RELEASED = "released"
    REQUEUED_BY_OPERATOR = "requeued_by_operator"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class TaskErrorCode(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    NOT_FOUND = "not_found"
    DEVICE_REVOKED = "device_revoked"
    INVALID_INPUT = "invalid_input"
    TIMED_OUT = "timed_out"
    WORKER_LOST = "worker_lost"
    FORBIDDEN = "forbidden"
    INTERNAL_ERROR = "internal_error"


RETRYABLE_CODES = frozenset(
    {
        TaskErrorCode.PROVIDER_UNAVAILABLE,
        TaskErrorCode.PROVIDER_TIMEOUT,
        TaskErrorCode.WORKER_LOST,
    }
)

SAFE_MESSAGES: dict[TaskErrorCode, str] = {
    TaskErrorCode.PROVIDER_UNAVAILABLE: "The assistant was temporarily unavailable.",
    TaskErrorCode.PROVIDER_TIMEOUT: "The assistant took too long to respond.",
    TaskErrorCode.PROVIDER_ERROR: "The assistant could not complete the request.",
    TaskErrorCode.BUDGET_EXCEEDED: "The daily usage budget has been reached.",
    TaskErrorCode.NOT_FOUND: "A resource referenced by the task was not found.",
    TaskErrorCode.DEVICE_REVOKED: "The device that created this task is no longer authorized.",
    TaskErrorCode.INVALID_INPUT: "The task input is invalid.",
    TaskErrorCode.TIMED_OUT: "The task exceeded its time limit.",
    TaskErrorCode.WORKER_LOST: "The worker running this task stopped responding.",
    TaskErrorCode.FORBIDDEN: "The device is not permitted to run this task.",
    TaskErrorCode.INTERNAL_ERROR: "An unexpected error occurred.",
}


def error_code_for(exc: BaseException) -> TaskErrorCode:
    """Most specific domain error first; anything unrecognized is an internal error."""
    if isinstance(exc, ProviderTimeoutError):
        return TaskErrorCode.PROVIDER_TIMEOUT
    if isinstance(exc, ProviderUnavailableError):
        return TaskErrorCode.PROVIDER_UNAVAILABLE
    if isinstance(exc, ProviderError):
        return TaskErrorCode.PROVIDER_ERROR
    if isinstance(exc, BudgetExceededError):
        return TaskErrorCode.BUDGET_EXCEEDED
    if isinstance(exc, NotFoundError):
        return TaskErrorCode.NOT_FOUND
    if isinstance(exc, DeviceRevokedError):
        return TaskErrorCode.DEVICE_REVOKED
    if isinstance(exc, InvalidTaskInputError):
        return TaskErrorCode.INVALID_INPUT
    if isinstance(exc, ForbiddenError):
        return TaskErrorCode.FORBIDDEN
    return TaskErrorCode.INTERNAL_ERROR


def backoff_seconds(
    attempt: int, base: float, cap: float, rng: random.Random | None = None
) -> float:
    """Exponential backoff with jitter for the retry after attempt number `attempt` (1-based)."""
    delay = min(cap, base * 2 ** max(0, attempt - 1))
    return float(delay * (rng or random).uniform(0.5, 1.0))
