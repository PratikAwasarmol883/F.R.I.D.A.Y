import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

MAX_MESSAGE_CHARS = 8000


class AskTaskInput(BaseModel):
    """Input of an `assistant.ask` task; validated at submission and again at execution."""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
    ]
    conversation_id: uuid.UUID | None = None
