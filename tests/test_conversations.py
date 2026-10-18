import uuid

from fastapi.testclient import TestClient

from friday.adapters.llm.fake import FakeLLMProvider
from friday.persistence.models import Conversation

from .conftest import Seeded, SessionFactory
from .helpers import count


def test_create_and_read_empty_conversation(client: TestClient, alice: Seeded) -> None:
    created = client.post(
        "/api/v1/conversations", json={"title": "  Plans  "}, headers=alice.headers
    )
    assert created.status_code == 201
    assert created.json()["title"] == "Plans"
    fetched = client.get(f"/api/v1/conversations/{created.json()['id']}", headers=alice.headers)
    assert fetched.status_code == 200
    assert fetched.json()["messages"] == []
    assert fetched.json()["next_before_sequence"] is None


def test_other_users_conversation_is_indistinguishable_from_missing(
    client: TestClient, alice: Seeded, bob: Seeded
) -> None:
    conversation_id = client.post("/api/v1/conversations", json={}, headers=alice.headers).json()[
        "id"
    ]
    foreign = client.get(f"/api/v1/conversations/{conversation_id}", headers=bob.headers)
    missing = client.get(f"/api/v1/conversations/{uuid.uuid4()}", headers=bob.headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    assert foreign.json()["message"] == missing.json()["message"]


def test_bob_cannot_ask_in_alices_conversation(
    client: TestClient,
    llm: FakeLLMProvider,
    alice: Seeded,
    bob: Seeded,
    session_factory: SessionFactory,
) -> None:
    conversation_id = client.post("/api/v1/conversations", json={}, headers=alice.headers).json()[
        "id"
    ]
    response = client.post(
        "/api/v1/assistant/ask",
        json={"conversation_id": conversation_id, "message": "hello"},
        headers=bob.headers,
    )
    assert response.status_code == 404
    assert llm.calls == []
    fetched = client.get(f"/api/v1/conversations/{conversation_id}", headers=alice.headers)
    assert fetched.json()["messages"] == []
    assert count(session_factory, Conversation) == 1


def test_pagination_returns_latest_messages_first_page(client: TestClient, alice: Seeded) -> None:
    conversation_id = None
    for index in range(3):  # 3 asks -> 6 messages
        body: dict[str, object] = {"message": f"q{index}"}
        if conversation_id:
            body["conversation_id"] = conversation_id
        conversation_id = client.post(
            "/api/v1/assistant/ask", json=body, headers=alice.headers
        ).json()["conversation_id"]

    page1 = client.get(
        f"/api/v1/conversations/{conversation_id}?limit=4", headers=alice.headers
    ).json()
    assert [m["sequence"] for m in page1["messages"]] == [3, 4, 5, 6]
    assert page1["next_before_sequence"] == 3

    page2 = client.get(
        f"/api/v1/conversations/{conversation_id}?limit=4&before_sequence=3",
        headers=alice.headers,
    ).json()
    assert [m["sequence"] for m in page2["messages"]] == [1, 2]
    assert page2["next_before_sequence"] is None


def test_pagination_limits_are_validated(client: TestClient, alice: Seeded) -> None:
    conversation_id = client.post("/api/v1/conversations", json={}, headers=alice.headers).json()[
        "id"
    ]
    for query in ("limit=0", "limit=201", "before_sequence=0"):
        response = client.get(
            f"/api/v1/conversations/{conversation_id}?{query}", headers=alice.headers
        )
        assert response.status_code == 422
