"""Approval rules. Pure: no I/O."""

import hashlib
import json
from enum import StrEnum
from typing import Any


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


def action_hash(tool_name: str, tool_version: str, arguments: dict[str, Any]) -> str:
    """Stable digest of exactly what will run: the tool, its version and canonical arguments."""
    canonical = json.dumps(
        {"tool": tool_name, "version": tool_version, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
