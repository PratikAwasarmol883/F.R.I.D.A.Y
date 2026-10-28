import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from friday import cli
from friday.core.security import hash_token
from friday.persistence.models import Device

from .conftest import Seeded, SessionFactory, run_db
from .helpers import audit_rows


def _kv(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def test_cli_flow_register_use_and_revoke(
    client: TestClient, session_factory: SessionFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(["create-user", "--name", "Zed", "--timezone", "Asia/Kolkata"], session_factory)
        == 0
    )
    user_id = _kv(capsys.readouterr().out)["user_id"]

    args = ["register-device", "--user-id", user_id, "--name", "mac", "--type", "mac"]
    assert cli.main(args, session_factory) == 0
    out = _kv(capsys.readouterr().out)
    token, device_id = out["token"], out["device_id"]

    async def stored(session: AsyncSession) -> list[Device]:
        return list((await session.scalars(select(Device))).all())

    [device] = run_db(session_factory, stored)
    assert device.token_hash == hash_token(token)
    assert token not in repr(vars(device))

    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/conversations", json={}, headers=headers).status_code == 201

    assert cli.main(["revoke-device", "--device-id", device_id], session_factory) == 0
    assert client.post("/api/v1/conversations", json={}, headers=headers).status_code == 401
    assert cli.main(["revoke-device", "--device-id", device_id], session_factory) == 1

    actions = [
        a
        for a in ("user.create", "device.register", "device.revoke")
        if audit_rows(session_factory, a)
    ]
    assert actions == ["user.create", "device.register", "device.revoke"]


def test_cli_rejects_bad_input(
    session_factory: SessionFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["create-user", "--name", "X", "--timezone", "Mars/Base"], session_factory) == 1
    unknown_user = "00000000-0000-0000-0000-000000000000"
    args = ["register-device", "--user-id", unknown_user, "--name", "d", "--type", "cli"]
    assert cli.main(args, session_factory) == 1
    assert "token=" not in capsys.readouterr().out


def test_tasks_listing_hides_input_and_requeue_only_accepts_failed_tasks(
    alice: Seeded, session_factory: SessionFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from friday.domain.task_state import TaskErrorCode, TaskStatus

    from .task_helpers import claim_one, new_task, tx

    failed = new_task(session_factory, alice, message="PRIVATEINPUT")
    queued = new_task(session_factory, alice)
    claim_one(session_factory)
    tx(
        session_factory,
        lambda r: r.finish(
            failed.id, "w1", 1, TaskStatus.FAILED, error_code=TaskErrorCode.PROVIDER_ERROR
        ),
    )

    assert cli.main(["tasks", "--user-id", str(alice.user_id)], session_factory) == 0
    listing = capsys.readouterr().out
    assert "PRIVATEINPUT" not in listing
    assert f"{failed.id} assistant.ask failed attempt=1/3 error=provider_error" in listing
    assert f"{queued.id} assistant.ask queued" in listing

    only_failed = ["tasks", "--user-id", str(alice.user_id), "--status", "failed"]
    assert cli.main(only_failed, session_factory) == 0
    assert str(queued.id) not in capsys.readouterr().out

    assert cli.main(["requeue-task", "--task-id", str(failed.id)], session_factory) == 0
    assert f"requeued={failed.id}" in capsys.readouterr().out
    assert (
        cli.main(["requeue-task", "--task-id", str(failed.id)], session_factory) == 1
    )  # queued now
    assert cli.main(["requeue-task", "--task-id", str(uuid.uuid4())], session_factory) == 1
