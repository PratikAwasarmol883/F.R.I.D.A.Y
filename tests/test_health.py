from fastapi.testclient import TestClient

from friday import __version__


def test_health_healthy(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert body["version"] == __version__
    assert "timestamp" in body


def test_health_degraded_does_not_leak_error_details(failing_db_client: TestClient) -> None:
    response = failing_db_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == "unavailable"
    assert "secret" not in response.text
