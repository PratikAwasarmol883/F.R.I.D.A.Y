import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_MESSAGE_CHARS = 8000


class AskTaskInput(BaseModel):
    """Input of an `assistant.ask` task; validated at submission and again at execution."""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
    ]
    conversation_id: uuid.UUID | None = None


MAX_QUESTION_CHARS = 1000


class ResearchTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
    ]
    max_sources: int | None = Field(default=None, ge=1, le=10, strict=True)
