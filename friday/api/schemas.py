"""API request/response schemas."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from friday.domain.schedule import ScheduleSpec, parse_timezone
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


class MemoryCreate(StrictModel):
    kind: Literal["preference", "fact", "project", "note"]
    value: str


class MemoryUpdate(StrictModel):
    kind: Literal["preference", "fact", "project", "note"] | None = None
    value: str | None = None


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    value: str
    status: str
    source: str
    created_at: datetime
    updated_at: datetime


class MemoryExport(BaseModel):
    exported_at: datetime
    memories: list[MemoryOut]


class ScheduledAskInput(AskTaskInput):
    conversation_id: None = None  # each run starts its own conversation


class AskScheduleTask(StrictModel):
    type: Literal["assistant.ask"]
    input: ScheduledAskInput


class ResearchScheduleTask(StrictModel):
    type: Literal["research.run"]
    input: ResearchTaskInput


ScheduleTask = Annotated[AskScheduleTask | ResearchScheduleTask, Field(discriminator="type")]
ScheduleName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


def _check_timezone(value: str | None) -> str | None:
    if value is not None:
        parse_timezone(value)
    return value


class ScheduleCreate(StrictModel):
    name: ScheduleName
    task: ScheduleTask
    schedule: ScheduleSpec
    timezone: str
    enabled: bool = True

    _zone = field_validator("timezone")(_check_timezone)


class ScheduleUpdate(StrictModel):
    name: ScheduleName | None = None
    enabled: bool | None = None
    schedule: ScheduleSpec | None = None
    timezone: str | None = None

    _zone = field_validator("timezone")(_check_timezone)

    @model_validator(mode="after")
    def _something_to_change(self) -> "ScheduleUpdate":
        if all(v is None for v in (self.name, self.enabled, self.schedule, self.timezone)):
            raise ValueError("nothing to update")
        return self


class ScheduleTaskOut(BaseModel):
    type: str
    input: dict[str, Any]


class ScheduleOut(BaseModel):
    id: uuid.UUID
    name: str
    task: ScheduleTaskOut
    schedule: ScheduleSpec
    timezone: str
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_task_id: uuid.UUID | None
    created_at: datetime


class ScheduleRunOut(BaseModel):
    task_id: uuid.UUID
    status: str
    scheduled_for: datetime | None
    finished_at: datetime | None
    error_code: str | None
    result: dict[str, Any] | None
