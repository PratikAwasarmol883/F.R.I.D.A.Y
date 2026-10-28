"""Research domain types shared by the tool layer, persistence, and the API."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class SourceRecord:
    """A page that was actually fetched during a run."""

    key: str
    url: str
    title: str
    retrieved_at: datetime
    content_hash: str
    excerpt: str
    content_type: str
    size_bytes: int


REQUIRED_SCOPES = frozenset({"tool:web_search", "tool:web_fetch"})
REQUIRED_TOOLS = frozenset({"web_search", "web_fetch"})


class ModelFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    confidence: Literal["supported", "uncertain"] = "uncertain"


class ModelConflict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(default_factory=list, max_length=10)


class ModelReport(BaseModel):
    """What the model must return; validated and then rewritten by the server."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=2000)
    findings: list[ModelFinding] = Field(default_factory=list, max_length=20)
    conflicts: list[ModelConflict] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)
