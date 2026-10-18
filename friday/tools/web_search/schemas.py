from pydantic import BaseModel, ConfigDict, Field

MAX_QUERY_CHARS = 200
MAX_RESULTS = 5


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=MAX_QUERY_CHARS, description="Plain-text search query."
    )
    max_results: int = Field(default=MAX_RESULTS, ge=1, le=MAX_RESULTS)


class SearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchOutput(BaseModel):
    results: list[SearchResultItem]
