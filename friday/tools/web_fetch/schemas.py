from pydantic import BaseModel, ConfigDict, Field

MAX_URL_CHARS = 2048


class WebFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=1,
        max_length=MAX_URL_CHARS,
        description="A URL taken from search results or from the user's question.",
    )


class WebFetchOutput(BaseModel):
    source_id: str
    url: str
    title: str
    retrieved_at: str
    text: str
