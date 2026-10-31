"""Device scopes that are not tied to a single tool."""

DECIDE_APPROVALS = "approvals:decide"
MANAGE_INTEGRATIONS = "integrations:manage"
PLATFORM_SCOPES = frozenset({DECIDE_APPROVALS, MANAGE_INTEGRATIONS})
