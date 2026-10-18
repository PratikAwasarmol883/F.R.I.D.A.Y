from enum import StrEnum


class DeviceType(StrEnum):
    MAC = "mac"
    PHONE = "phone"
    WEB = "web"
    CLI = "cli"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class RiskClass(StrEnum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    CONSEQUENTIAL = "consequential"
    DESTRUCTIVE = "destructive"


class ToolStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
