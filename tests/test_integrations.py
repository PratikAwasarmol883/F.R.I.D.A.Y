from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from friday.core.config import Settings
from friday.core.credentials import CredentialBox, CredentialError

from .approval_helpers import DECIDER, INTEGRATIONS, KEY, SETTINGS, TOPIC, add_device, connect
from .conftest import Seeded, SessionFactory, make_user
from .helpers import audit_rows
from .task_helpers import sql


@pytest.fixture
def owner(session_factory: SessionFactory) -> Seeded:
    return make_user(session_factory, "alice", DECIDER)


def test_connect_stores_an_encrypted_credential_and_never_returns_it(
    make_client: Any, owner: Seeded, test_db_url: str
) -> None:
    client = make_client(**SETTINGS)
    response = connect(client, owner)
    assert response.status_code == 201
    assert set(response.json()) == {"provider", "status", "created_at", "revoked_at"}
    assert TOPIC not in response.text
    [(stored,)] = sql(test_db_url, "SELECT credential FROM integration_connections")
    assert stored != TOPIC and TOPIC not in stored
    assert CredentialBox(SecretStr(KEY)).open(stored) == TOPIC
    listing = client.get(INTEGRATIONS, headers=owner.headers)
    assert [(c["provider"], c["status"]) for c in listing.json()] == [("ntfy", "active")]
    assert TOPIC not in listing.text


def test_a_wrong_key_cannot_decrypt() -> None:
    sealed = CredentialBox(SecretStr(KEY)).seal("secret-topic-123")
    with pytest.raises(CredentialError):
        CredentialBox(SecretStr(Fernet.generate_key().decode())).open(sealed)


def test_connect_replaces_the_active_connection(
    make_client: Any, owner: Seeded, test_db_url: str
) -> None:
    client = make_client(**SETTINGS)
    connect(client, owner, "first-topic-12345")
    connect(client, owner, "second-topic-1234")
    rows = sql(test_db_url, "SELECT status FROM integration_connections ORDER BY created_at")
    assert [r[0] for r in rows] == ["revoked", "active"]
    assert sql(
        test_db_url, "SELECT count(*) FROM integration_connections WHERE credential IS NULL"
    ) == [(1,)]


def test_revoke_deletes_the_credential_and_is_final(
    make_client: Any, owner: Seeded, test_db_url: str, session_factory: SessionFactory
) -> None:
    client = make_client(**SETTINGS)
    connect(client, owner)
    assert client.delete(f"{INTEGRATIONS}/ntfy", headers=owner.headers).status_code == 204
    rows = sql(test_db_url, "SELECT status, credential FROM integration_connections")
    assert rows == [("revoked", None)]
    assert client.delete(f"{INTEGRATIONS}/ntfy", headers=owner.headers).status_code == 404
    assert [a.target_id for a in audit_rows(session_factory, "integration.revoke")] == ["ntfy"]


def test_without_a_server_key_connecting_is_refused(
    make_client: Any, owner: Seeded, test_db_url: str
) -> None:
    response = connect(make_client(), owner)
    assert response.status_code == 503 and response.json()["code"] == "INTEGRATION_UNAVAILABLE"
    assert sql(test_db_url, "SELECT count(*) FROM integration_connections") == [(0,)]


def test_an_empty_server_key_counts_as_unconfigured(make_client: Any, owner: Seeded) -> None:
    response = connect(make_client(integration_key=SecretStr("")), owner)
    assert response.status_code == 503 and response.json()["code"] == "INTEGRATION_UNAVAILABLE"


def test_management_needs_the_scope(
    make_client: Any, owner: Seeded, session_factory: SessionFactory
) -> None:
    client = make_client(**SETTINGS)
    talker = add_device(session_factory, owner, "voice", ["tool:send_notification"])
    assert connect(client, talker).status_code == 403
    connect(client, owner)
    assert client.delete(f"{INTEGRATIONS}/ntfy", headers=talker.headers).status_code == 403
    listing = client.get(INTEGRATIONS, headers=talker.headers).json()
    assert [c["status"] for c in listing] == ["active"]


@pytest.mark.parametrize(
    "topic",
    ["", "short", "has space in it", "slash/topic/1234", "x" * 65, "ünïcode-topic-1"],
)
def test_topic_validation(make_client: Any, owner: Seeded, topic: str) -> None:
    response = connect(make_client(**SETTINGS), owner, topic)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_unknown_providers_and_extra_fields(make_client: Any, owner: Seeded) -> None:
    client = make_client(**SETTINGS)
    other = client.post(
        f"{INTEGRATIONS}/gmail/connect", json={"topic": TOPIC}, headers=owner.headers
    )
    assert other.status_code == 404
    extra = client.post(
        f"{INTEGRATIONS}/ntfy/connect",
        json={"topic": TOPIC, "url": "http://x"},
        headers=owner.headers,
    )
    assert extra.status_code == 422
    assert client.delete(f"{INTEGRATIONS}/gmail", headers=owner.headers).status_code == 404


def test_connections_are_private_and_authenticated(
    make_client: Any, owner: Seeded, session_factory: SessionFactory
) -> None:
    client = make_client(**SETTINGS)
    connect(client, owner)
    stranger = make_user(session_factory, "bob", DECIDER)
    assert client.get(INTEGRATIONS, headers=stranger.headers).json() == []
    assert client.delete(f"{INTEGRATIONS}/ntfy", headers=stranger.headers).status_code == 404
    assert client.get(INTEGRATIONS).status_code == 401


def test_audit_never_contains_the_topic(
    make_client: Any, owner: Seeded, session_factory: SessionFactory
) -> None:
    connect(make_client(**SETTINGS), owner)
    [row] = audit_rows(session_factory, "integration.connect")
    assert TOPIC not in repr(vars(row))


def test_invalid_server_keys_fail_at_startup() -> None:
    with pytest.raises(ValueError, match="Fernet"):
        Settings(_env_file=None, integration_key=SecretStr("not-a-key"))


def test_notify_base_url_rules() -> None:
    local = Settings(_env_file=None, notify_base_url="http://localhost:8080/")
    assert local.notify_base_url == "http://localhost:8080"
    for bad in (
        "http://ntfy.example.com",
        "https://u:p@ntfy.sh",
        "https://ntfy.sh/?x=1",
        "ftp://x.y",
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, notify_base_url=bad)
