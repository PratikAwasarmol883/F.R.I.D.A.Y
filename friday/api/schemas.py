"""API request/response schemas."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from friday.domain.task_inputs import MAX_MESSAGE_CHARS, AskTaskInput, ResearchTaskInput  # noqa: F401


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(BaseModel):
    status: str
    database: str
    timestamp: datetime
    version: str


class ConversationCreate(StrictModel):
    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    ) = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    role: str
    content: str
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    next_before_sequence: int | None


class AskRequest(StrictModel):
    conversation_id: uuid.UUID | None = None
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
    ]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class SourceOut(BaseModel):
    title: str
    url: str


class ToolUseOut(BaseModel):
    name: str
    status: str


class AudioOut(BaseModel):
    format: str
    base64: str


class VoiceResponse(BaseModel):
    transcript: str
    answer: str
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    usage: Usage
    sources: list[SourceOut] = []
    tools_used: list[ToolUseOut] = []
    audio: AudioOut | None = None
    speech_error: str | None = None


class AskResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    usage: Usage
    sources: list[SourceOut] = []
    tools_used: list[ToolUseOut] = []


class AskTaskCreate(StrictModel):
    type: Literal["assistant.ask"]
    input: AskTaskInput


TaskCreate = AskTaskCreate  # becomes a discriminated union as task types are added


class TaskResultOut(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str | None
    usage: Usage
    sources: list[SourceOut] = []
    tools_used: list[ToolUseOut] = []


class ResearchResultOut(BaseModel):
    report_id: uuid.UUID
    usage: Usage
    sources_count: int


class TaskOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    attempt: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    result: TaskResultOut | ResearchResultOut | None


class TaskEventOut(BaseModel):
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class TaskEventsOut(BaseModel):
    events: list[TaskEventOut]
    next_after_sequence: int


class ReportSourceRef(BaseModel):
    key: str
    url: str
    title: str
    retrieved_at: datetime


class ReportSourceOut(ReportSourceRef):
    content_hash: str


class ReportFindingOut(BaseModel):
    claim: str
    confidence: str
    sources: list[ReportSourceRef]


class ReportConflictOut(BaseModel):
    description: str
    sources: list[ReportSourceRef]


class ReportOut(BaseModel):
    id: uuid.UUID
    question: str
    created_at: datetime
    summary: str
    findings: list[ReportFindingOut]
    conflicts: list[ReportConflictOut]
    limitations: list[str]
    sources: list[ReportSourceOut]
    markdown: str
