"""Environment-based configuration, validated at startup."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from friday.core.credentials import is_valid_key


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

    enabled_tools: str = (
        "web_search,web_fetch,remember,send_notification"  # comma-separated kill switch
    )
    search_provider: Literal["tavily"] = "tavily"
    search_api_key: SecretStr | None = None
    tool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    max_tool_calls_per_ask: int = Field(default=3, ge=1, le=10)
    search_max_results: int = Field(default=5, ge=1, le=5)
    search_daily_limit: int = Field(default=50, ge=1)
    ask_deadline_seconds: float = Field(default=60.0, gt=0, le=300)

    speech_api_key: SecretStr | None = None
    speech_base_url: str = "https://api.openai.com/v1"
    stt_model: str = "whisper-1"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    voice_max_upload_bytes: int = Field(default=6_000_000, ge=1_000, le=25_000_000)
    voice_daily_limit: int = Field(default=100, ge=1)
    voice_tts_max_chars: int = Field(default=1500, ge=50, le=4000)
    voice_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    memory_enabled: bool = True
    memory_in_prompt: bool = True
    memory_max_notes: int = Field(default=200, ge=1, le=10_000)
    memory_max_pending: int = Field(default=20, ge=1, le=1_000)
    memory_prompt_max_notes: int = Field(default=8, ge=1, le=50)
    memory_prompt_max_chars: int = Field(default=1500, ge=100, le=20_000)

    max_schedules_per_user: int = Field(default=10, ge=1, le=1_000)
    schedule_grace_seconds: float = Field(default=3600.0, ge=0, le=86_400)
    scheduler_batch: int = Field(default=20, ge=1, le=500)

    integration_key: SecretStr | None = None  # Fernet key; integrations are refused without it
    notify_base_url: str = "https://ntfy.sh"
    notify_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    approval_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    max_pending_approvals: int = Field(default=10, ge=1, le=1_000)
    approval_retention_days: int = Field(default=30, ge=1, le=3650)
    approval_exec_stale_seconds: int = Field(default=300, ge=30, le=3600)
    notification_daily_limit: int = Field(default=20, ge=1)

    fetch_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    fetch_daily_limit: int = Field(default=100, ge=1)
    research_max_searches: int = Field(default=3, ge=1, le=10)
    research_max_sources: int = Field(default=5, ge=1, le=10)
    research_max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    research_deadline_seconds: float = Field(default=240.0, gt=0, le=290)

    task_worker_concurrency: int = Field(default=2, ge=1, le=32)
    task_poll_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    task_lease_seconds: float = Field(default=60.0, gt=0, le=3600)
    task_heartbeat_seconds: float = Field(default=10.0, gt=0, le=600)
    task_max_attempts: int = Field(default=3, ge=1, le=10)
    task_max_runtime_seconds: int = Field(default=300, ge=1, le=3600)
    task_retry_base_seconds: float = Field(default=5.0, gt=0, le=3600)
    task_retry_max_seconds: float = Field(default=120.0, gt=0, le=86400)
    max_active_tasks_per_user: int = Field(default=5, ge=1, le=100)
    task_retention_days: int = Field(default=30, ge=1, le=3650)
    task_shutdown_grace_seconds: float = Field(default=30.0, ge=0, le=300)

    @field_validator("speech_base_url")
    @classmethod
    def _safe_speech_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("SPEECH_BASE_URL must be https (http only for localhost)")
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("SPEECH_BASE_URL must be a plain URL without credentials or query")
        return value.rstrip("/")

    @field_validator("notify_base_url")
    @classmethod
    def _safe_notify_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("NOTIFY_BASE_URL must be https (http only for localhost)")
        if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("NOTIFY_BASE_URL must be a plain URL without credentials or query")
        return value.rstrip("/")

    @field_validator("integration_key")
    @classmethod
    def _valid_integration_key(cls, value: SecretStr | None) -> SecretStr | None:
        if (
            value is not None
            and value.get_secret_value()
            and not is_valid_key(value.get_secret_value())
        ):
            raise ValueError("INTEGRATION_KEY must be a Fernet key (urlsafe base64, 32 bytes)")
        return value

    @model_validator(mode="after")
    def _lease_outlives_heartbeat(self) -> "Settings":
        if self.task_lease_seconds <= 2 * self.task_heartbeat_seconds:
            raise ValueError("TASK_LEASE_SECONDS must be greater than 2x TASK_HEARTBEAT_SECONDS")
        if self.task_retry_max_seconds < self.task_retry_base_seconds:
            raise ValueError("TASK_RETRY_MAX_SECONDS must be >= TASK_RETRY_BASE_SECONDS")
        return self

    @property
    def anthropic_key_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value())

    @property
    def enabled_tool_names(self) -> frozenset[str]:
        return frozenset(name.strip() for name in self.enabled_tools.split(",") if name.strip())

    @property
    def integrations_configured(self) -> bool:
        return bool(self.integration_key and self.integration_key.get_secret_value())

    @property
    def speech_configured(self) -> bool:
        return bool(self.speech_api_key and self.speech_api_key.get_secret_value())

    @property
    def search_key_configured(self) -> bool:
        return bool(self.search_api_key and self.search_api_key.get_secret_value())


settings = Settings()


def get_settings() -> Settings:
    return settings
