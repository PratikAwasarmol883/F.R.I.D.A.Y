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
