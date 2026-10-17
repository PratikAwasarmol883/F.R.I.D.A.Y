"""Environment-based configuration, validated at startup."""

from typing import Literal

from pydantic import Field
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


settings = Settings()
