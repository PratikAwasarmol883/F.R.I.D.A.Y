"""Environment-based configuration, validated at startup."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "postgresql://friday_user:friday_password@localhost:5433/friday_db"
    sqlalchemy_echo: bool = False
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-haiku-5-5"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    max_history_messages: int = Field(default=20, ge=0, le=100)
    daily_token_budget: int = Field(default=200_000, ge=1)

    enabled_tools: str = "web_search"  # comma-separated kill switch
    search_provider: Literal["tavily"] = "tavily"
    search_api_key: SecretStr | None = None
    tool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_tool_calls_per_ask: int = Field(default=3, ge=1, le=10)
    search_max_results: int = Field(default=5, ge=1, le=5)
    search_daily_limit: int = Field(default=50, ge=1)
    ask_deadline_seconds: float = Field(default=60.0, gt=0, le=300)

    @property
    def anthropic_key_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())

    @property
    def enabled_tool_names(self) -> frozenset[str]:
        return frozenset(name.strip() for name in self.enabled_tools.split(",") if name.strip())

    @property
    def search_key_configured(self) -> bool:
        return bool(self.search_api_key and self.search_api_key.get_secret_value())


settings = Settings()


def get_settings() -> Settings:
    return settings
