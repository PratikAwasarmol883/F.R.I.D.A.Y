"""Masking of secret-shaped strings so they never reach audit rows or logs."""

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_PATTERNS = [
    re.compile(r"\bfri_[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"[A-Za-z0-9_+/=-]{32,}"),  # long high-entropy runs
]


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def redact_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact_value(value: Any, redacted_fields: Iterable[str] = ()) -> Any:
    """Recursively mask named fields and secret-shaped strings; returns a new structure."""
    blocked = frozenset(redacted_fields)
    if isinstance(value, dict):
        return {
            key: REDACTED if key in blocked else redact_value(item, blocked)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, blocked) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Stable hash of the validated arguments, so repeated calls can be correlated."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
