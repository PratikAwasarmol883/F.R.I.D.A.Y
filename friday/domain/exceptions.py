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
