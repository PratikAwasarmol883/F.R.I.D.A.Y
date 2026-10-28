class DomainError(Exception):
    """Base class for expected, user-safe failures."""


class NotAuthenticatedError(DomainError):
    """Missing, malformed, unknown, or revoked credentials (deliberately indistinguishable)."""


class NotFoundError(DomainError):
    """Resource is missing or not owned by the caller (deliberately indistinguishable)."""


class BudgetExceededError(DomainError):
    """The user's daily token budget is used up."""


class ProviderError(DomainError):
    """The LLM provider failed in a way that is not more specifically classified."""


class ProviderTimeoutError(ProviderError):
    """The LLM provider call exceeded its time limit."""


class ProviderUnavailableError(ProviderError):
    """The LLM provider is rate-limited, overloaded, unreachable, or not configured."""


class TaskLimitError(DomainError):
    """The user already has the maximum number of active tasks."""


class IdempotencyConflictError(DomainError):
    """An idempotency key was reused with a different request."""


class TaskNotCancellableError(DomainError):
    """The task is already in a terminal state other than cancelled."""


class DeviceRevokedError(DomainError):
    """The device that created a task is revoked or gone."""


class InvalidTaskInputError(DomainError):
    """A stored task input no longer validates."""


class ForbiddenError(DomainError):
    """The caller is authenticated but lacks the scope for this operation."""


class ReportFormatError(ProviderError):
    """The model did not return a valid structured report."""


class PayloadTooLargeError(DomainError):
    """An upload exceeds the size limit."""


class UnsupportedMediaError(DomainError):
    """An upload is not a supported audio format."""


class NoSpeechError(DomainError):
    """The transcription contained no usable speech."""


class VoiceLimitError(DomainError):
    """The user's daily voice request limit is used up."""
