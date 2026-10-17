from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from friday.app import create_app
from friday.persistence.database import get_db_session


class FakeSession:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def execute(self, *_: object, **__: object) -> None:
        if self.fail:
            raise RuntimeError("secret connection string leaked here")


def make_client(fail: bool) -> Iterator[TestClient]:
    app = create_app()

    async def override() -> AsyncGenerator[AsyncSession, None]:
        yield FakeSession(fail=fail)  # type: ignore[misc]

    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from make_client(fail=False)


@pytest.fixture
def failing_db_client() -> Iterator[TestClient]:
    yield from make_client(fail=True)
