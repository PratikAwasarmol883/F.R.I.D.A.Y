import pytest
from pydantic import ValidationError

from friday.core.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.log_level == "INFO"
    assert s.port == 8000


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("PORT", "9000")
    s = Settings(_env_file=None)
    assert s.log_level == "DEBUG"
    assert s.port == 9000


def test_invalid_log_level_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_port_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
