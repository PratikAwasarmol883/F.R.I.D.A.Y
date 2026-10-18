import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.security import TOKEN_PREFIX
from friday.persistence.models import Device
from friday.services.auth_service import AuthService

from .conftest import Seeded, SessionFactory, run_db


def _without_request_id(response_json: dict) -> dict:  # type: ignore[type-arg]
    return {k: v for k, v in response_json.items() if k != "request_id"}


def test_missing_header_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/conversations", json={})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_every_credential_failure_looks_identical(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    async def revoke(session: AsyncSession) -> None:
        await AuthService(session).revoke_device(alice.device_id)

    run_db(session_factory, revoke)

    attempts = [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer not-a-friday-token"},
        {"Authorization": f"Bearer {TOKEN_PREFIX}{uuid.uuid4().hex}"},
        alice.headers,  # valid token, but the device is revoked
    ]
    bodies = []
    for headers in attempts:
        response = client.post("/api/v1/conversations", json={}, headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        bodies.append(_without_request_id(response.json()))
    assert all(body == bodies[0] for body in bodies)


def test_valid_token_is_accepted(client: TestClient, alice: Seeded) -> None:
    response = client.post("/api/v1/conversations", json={}, headers=alice.headers)
    assert response.status_code == 201


def test_last_seen_is_set_and_throttled(
    client: TestClient, alice: Seeded, session_factory: SessionFactory
) -> None:
    async def last_seen(session: AsyncSession) -> object:
        device = await session.get(Device, alice.device_id)
        assert device is not None
        return device.last_seen_at

    assert run_db(session_factory, last_seen) is None
    client.post("/api/v1/conversations", json={}, headers=alice.headers)
    first = run_db(session_factory, last_seen)
    assert first is not None
    client.post("/api/v1/conversations", json={}, headers=alice.headers)
    assert run_db(session_factory, last_seen) == first


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_request_id_is_propagated_or_replaced(client: TestClient) -> None:
    ok = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})
    assert ok.headers["X-Request-ID"] == "trace-123"
    bad = client.get("/api/v1/health", headers={"X-Request-ID": "bad id with spaces!"})
    assert bad.headers["X-Request-ID"] != "bad id with spaces!"
    err = client.post("/api/v1/conversations", json={})
    assert err.json()["request_id"] == err.headers["X-Request-ID"]
