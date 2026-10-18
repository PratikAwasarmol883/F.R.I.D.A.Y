"""API request/response schemas."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

MAX_MESSAGE_CHARS = 8000


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


class AskResponse(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    usage: Usage
