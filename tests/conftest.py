import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from friday.adapters.llm.fake import FakeLLMProvider
from friday.api.dependencies import get_llm_provider, get_tool_registry
from friday.app import create_app
from friday.core.config import Settings, get_settings, settings
from friday.core.logging_config import JSONFormatter
from friday.domain.enums import DeviceType
from friday.orchestration.tool_registry import ToolRegistry
from friday.persistence.database import _driver_url, get_db_session
from friday.services.auth_service import AuthService

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = "friday_test"
TABLES = "users, devices, conversations, messages, usage_records, audit_events, tasks, task_events"
T = TypeVar("T")

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class Seeded:
    user_id: Any
    device_id: Any
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class FakeSession:
    """Stands in for the DB in health-only tests."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def execute(self, *_: object, **__: object) -> None:
        if self.fail:
            raise RuntimeError("secret connection string leaked here")


@pytest.fixture(scope="session")
def test_db_url() -> str:
    base = make_url(settings.database_url)
    admin_dsn = base.set(database="postgres").render_as_string(hide_password=False)
    test_dsn = base.set(database=TEST_DB).render_as_string(hide_password=False)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if exists.fetchone() is None:
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _driver_url(test_dsn).replace("%", "%%"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return test_dsn


@pytest.fixture
def session_factory(test_db_url: str) -> SessionFactory:
    with psycopg.connect(test_db_url) as conn:
        conn.execute(f"TRUNCATE {TABLES} CASCADE")
    engine = create_async_engine(_driver_url(test_db_url), poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


def run_db(factory: SessionFactory, fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def go() -> T:
        async with factory() as session:
            return await fn(session)

    return asyncio.run(go())


def make_user(
    factory: SessionFactory, name: str = "alice", scopes: list[str] | None = None
) -> Seeded:
    async def go(session: AsyncSession) -> Seeded:
        auth = AuthService(session)
        user = await auth.create_user(name, "UTC")
        device, token = await auth.register_device(
            user.id, f"{name}-device", DeviceType.CLI, scopes
        )
        return Seeded(user.id, device.id, token)

    return run_db(factory, go)


@pytest.fixture
def alice(session_factory: SessionFactory) -> Seeded:
    return make_user(session_factory, "alice")


@pytest.fixture
def bob(session_factory: SessionFactory) -> Seeded:
    return make_user(session_factory, "bob")


@pytest.fixture
def alice_tools(session_factory: SessionFactory) -> Seeded:
    return make_user(session_factory, "alice", ["tool:web_search"])


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


BuildApp = Callable[..., Any]


@pytest.fixture
def build_app(session_factory: SessionFactory) -> BuildApp:
    def build(
        llm: FakeLLMProvider | None = None, registry: ToolRegistry | None = None, **overrides: Any
    ) -> Any:
        app = create_app()
        test_settings = Settings(_env_file=None).model_copy(update=overrides)

        async def db_override() -> AsyncGenerator[AsyncSession, None]:
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = db_override
        app.dependency_overrides[get_settings] = lambda: test_settings
        if llm is not None:
            app.dependency_overrides[get_llm_provider] = lambda: llm
        if registry is not None:
            app.dependency_overrides[get_tool_registry] = lambda: registry
        return app

    return build


@pytest.fixture
def client(build_app: BuildApp, llm: FakeLLMProvider) -> Iterator[TestClient]:
    with TestClient(build_app(llm), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def make_client(build_app: BuildApp) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(
        llm: FakeLLMProvider | None = None, registry: ToolRegistry | None = None, **overrides: Any
    ) -> TestClient:
        test_client = TestClient(
            build_app(llm, registry, **overrides), raise_server_exceptions=False
        )
        clients.append(test_client)
        return test_client

    yield factory
    for test_client in clients:
        test_client.close()


def make_health_client(fail: bool) -> Iterator[TestClient]:
    app = create_app()

    async def override() -> AsyncGenerator[AsyncSession, None]:
        yield FakeSession(fail=fail)  # type: ignore[misc]

    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_db_client() -> Iterator[TestClient]:
    yield from make_health_client(fail=False)


@pytest.fixture
def failing_db_client() -> Iterator[TestClient]:
    yield from make_health_client(fail=True)


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []
        self.setFormatter(JSONFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def log_lines(client: TestClient) -> Iterator[list[str]]:
    """Collect formatted JSON log lines (create_app() resets root handlers, so attach after)."""
    collector = _Collector()
    root = logging.getLogger()
    root.addHandler(collector)
    yield collector.lines
    root.removeHandler(collector)


@pytest.fixture
def client_ask(client: TestClient, alice: Seeded) -> tuple[str, str]:
    body = client.post(
        "/api/v1/assistant/ask", json={"message": "seed"}, headers=alice.headers
    ).json()
    return body["conversation_id"], body["message_id"]
