from fastapi.testclient import TestClient

from .conftest import Seeded


def test_logs_never_contain_tokens_or_message_content(
    client: TestClient, log_lines: list[str], alice: Seeded
) -> None:
    client.post("/api/v1/assistant/ask", json={"message": "PRIVATEMESSAGE"}, headers=alice.headers)
    client.post(
        "/api/v1/assistant/ask",
        json={"message": "PRIVATEMESSAGE"},
        headers={"Authorization": "Bearer fri_wrongtoken123"},
    )
    joined = "\n".join(log_lines)
    assert log_lines, "expected log output"
    assert alice.token not in joined
    assert "fri_wrongtoken123" not in joined
    assert "PRIVATEMESSAGE" not in joined
    assert '"outcome": "success"' in joined
