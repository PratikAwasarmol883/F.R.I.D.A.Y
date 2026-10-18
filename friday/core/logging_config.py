"""Structured JSON logging. Never pass message content, tokens, or secrets to the logger."""

import json
import logging
import sys
from typing import Any

from friday.core.config import settings

_EXTRA_FIELDS = (
    "task_id",
    "user_id",
    "conversation_id",
    "model",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "outcome",
)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from friday.core.request_context import request_id_var

        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        request_id = request_id_var.get()
        if request_id:
            log_data["request_id"] = request_id
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                log_data[field] = str(getattr(record, field))
        return json.dumps(log_data)


def setup_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(settings.log_level)
    console_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(console_handler)

    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
