import itertools
import random

import pytest
from pydantic import ValidationError

from friday.core.config import Settings
from friday.domain.exceptions import (
    BudgetExceededError,
    DeviceRevokedError,
    NotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from friday.domain.task_state import (
    ACTIVE_STATUSES,
    RETRYABLE_CODES,
    SAFE_MESSAGES,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    TaskErrorCode,
    TaskStatus,
    assert_transition,
    backoff_seconds,
    can_transition,
    error_code_for,
)

S = TaskStatus
LEGAL = {
    (S.QUEUED, S.RUNNING),
    (S.QUEUED, S.CANCELLED),
    (S.RUNNING, S.SUCCEEDED),
    (S.RUNNING, S.FAILED),
    (S.RUNNING, S.TIMED_OUT),
    (S.RUNNING, S.CANCEL_REQUESTED),
    (S.RUNNING, S.CANCELLED),
    (S.RUNNING, S.QUEUED),
    (S.CANCEL_REQUESTED, S.CANCELLED),
}


@pytest.mark.parametrize(("current", "target"), list(itertools.product(S, S)))
def test_every_pair_matches_the_documented_table(current: S, target: S) -> None:
    expected = (current, target) in LEGAL
    assert can_transition(current, target) is expected
    if expected:
        assert_transition(current, target)
    else:
        with pytest.raises(InvalidTransitionError):
            assert_transition(current, target)


def test_terminal_states_have_no_exits_except_operator_requeue() -> None:
    for terminal in TERMINAL_STATUSES:
        assert not any(can_transition(terminal, target) for target in S)
        operator_exits = [t for t in S if can_transition(terminal, t, operator=True)]
        if terminal in {S.FAILED, S.TIMED_OUT}:
            assert operator_exits == [S.QUEUED]
        else:
            assert operator_exits == []


def test_status_groups_are_consistent() -> None:
    assert TERMINAL_STATUSES | ACTIVE_STATUSES == set(S)
    assert not TERMINAL_STATUSES & ACTIVE_STATUSES


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ProviderTimeoutError(), TaskErrorCode.PROVIDER_TIMEOUT),
        (ProviderUnavailableError(), TaskErrorCode.PROVIDER_UNAVAILABLE),
        (ProviderError(), TaskErrorCode.PROVIDER_ERROR),
        (BudgetExceededError(), TaskErrorCode.BUDGET_EXCEEDED),
        (NotFoundError(), TaskErrorCode.NOT_FOUND),
        (DeviceRevokedError(), TaskErrorCode.DEVICE_REVOKED),
        (RuntimeError("secret"), TaskErrorCode.INTERNAL_ERROR),
    ],
)
def test_exceptions_map_to_stable_codes(exc: Exception, code: TaskErrorCode) -> None:
    assert error_code_for(exc) is code


def test_only_transient_codes_are_retryable_and_every_code_has_a_safe_message() -> None:
    assert RETRYABLE_CODES == {
        TaskErrorCode.PROVIDER_UNAVAILABLE,
        TaskErrorCode.PROVIDER_TIMEOUT,
        TaskErrorCode.WORKER_LOST,
    }
    assert set(SAFE_MESSAGES) == set(TaskErrorCode)
    assert all("secret" not in message.lower() for message in SAFE_MESSAGES.values())


def test_backoff_grows_is_capped_and_jittered() -> None:
    rng = random.Random(7)
    for attempt, ceiling in [(1, 5), (2, 10), (3, 20), (4, 40), (5, 80), (6, 120), (20, 120)]:
        samples = [backoff_seconds(attempt, 5, 120, rng) for _ in range(200)]
        assert all(ceiling * 0.5 <= value <= ceiling for value in samples)
        assert max(samples) > min(samples)  # jitter actually varies
    assert backoff_seconds(0, 5, 120, rng) <= 5  # defensive: attempt < 1 behaves like 1


def test_settings_defaults_are_valid_and_lease_must_outlive_two_heartbeats() -> None:
    Settings(_env_file=None)
    with pytest.raises(ValidationError, match="TASK_LEASE_SECONDS"):
        Settings(_env_file=None, task_lease_seconds=20, task_heartbeat_seconds=10)
    Settings(_env_file=None, task_lease_seconds=21, task_heartbeat_seconds=10)
    with pytest.raises(ValidationError, match="TASK_RETRY_MAX_SECONDS"):
        Settings(_env_file=None, task_retry_base_seconds=30, task_retry_max_seconds=10)
