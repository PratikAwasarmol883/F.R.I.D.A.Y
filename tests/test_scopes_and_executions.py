import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from friday import cli
from friday.domain.enums import DeviceType, RiskClass, ToolStatus
from friday.domain.exceptions import NotAuthenticatedError, NotFoundError
from friday.persistence.models import ToolExecution
from friday.persistence.repositories.tool_executions import ToolExecutionRepository
from friday.services.auth_service import AuthService

from .conftest import Seeded, SessionFactory, make_user, run_db


def test_new_devices_have_no_scopes_by_default(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    async def go(session: AsyncSession) -> frozenset[str]:
        return (await AuthService(session).authenticate(alice.token)).scopes

    assert run_db(session_factory, go) == frozenset()


def test_registered_scopes_are_normalized_and_returned_on_authenticate(
    session_factory: SessionFactory,
) -> None:
    async def go(session: AsyncSession) -> frozenset[str]:
        auth = AuthService(session)
        user = await auth.create_user("zed", "UTC")
        _, token = await auth.register_device(
            user.id, "d", DeviceType.CLI, [" tool:web_search ", "tool:web_search", ""]
        )
        return (await auth.authenticate(token)).scopes

    assert run_db(session_factory, go) == frozenset({"tool:web_search"})


@pytest.mark.parametrize("bad", ["web_search", "tool:", "tool:Bad Name", "admin", "tool:x"])
def test_invalid_scopes_are_rejected(session_factory: SessionFactory, bad: str) -> None:
    async def go(session: AsyncSession) -> None:
        auth = AuthService(session)
        user = await auth.create_user("zed", "UTC")
        await auth.register_device(user.id, "d", DeviceType.CLI, [bad])

    with pytest.raises(ValueError, match="Invalid scope"):
        run_db(session_factory, go)


def test_set_scopes_replaces_audits_and_rejects_revoked_devices(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    async def grant(session: AsyncSession) -> frozenset[str]:
        auth = AuthService(session)
        await auth.set_device_scopes(alice.device_id, ["tool:web_search"])
        granted = (await auth.authenticate(alice.token)).scopes
        await auth.set_device_scopes(alice.device_id, [])
        assert (await auth.authenticate(alice.token)).scopes == frozenset()
        return granted

    assert run_db(session_factory, grant) == frozenset({"tool:web_search"})

    async def revoked(session: AsyncSession) -> None:
        auth = AuthService(session)
        await auth.revoke_device(alice.device_id)
        with pytest.raises(NotFoundError):
            await auth.set_device_scopes(alice.device_id, ["tool:web_search"])
        with pytest.raises(NotAuthenticatedError):
            await auth.authenticate(alice.token)

    run_db(session_factory, revoked)

    from .helpers import audit_rows

    assert len(audit_rows(session_factory, "device.scopes_update")) == 2


def test_cli_scopes_and_tool_log(
    session_factory: SessionFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    seeded = make_user(session_factory, "carol")
    args = ["register-device", "--user-id", str(seeded.user_id), "--name", "m", "--type", "mac"]
    assert cli.main([*args, "--scopes", "tool:web_search"], session_factory) == 0
    device_id = next(
        line.split("=", 1)[1]
        for line in capsys.readouterr().out.splitlines()
        if "device_id=" in line
    )
    assert (
        cli.main(["set-device-scopes", "--device-id", device_id, "--scopes", ""], session_factory)
        == 0
    )
    assert "scopes=\n" in capsys.readouterr().out
    assert (
        cli.main(
            ["set-device-scopes", "--device-id", device_id, "--scopes", "nope"], session_factory
        )
        == 1
    )

    async def seed_execution(session: AsyncSession) -> None:
        await ToolExecutionRepository(session).add(
            user_id=seeded.user_id,
            device_id=seeded.device_id,
            conversation_id=None,
            tool_name="web_search",
            tool_version="1",
            risk_class=str(RiskClass.READ_ONLY),
            status=ToolStatus.SUCCEEDED,
            arguments_redacted={"query": "python"},
            arguments_hash="0" * 64,
            result_meta={"count": 2},
            error_code=None,
            duration_ms=12,
        )
        await session.commit()

    run_db(session_factory, seed_execution)
    assert cli.main(["tool-log", "--user-id", str(seeded.user_id)], session_factory) == 0
    out = capsys.readouterr().out
    assert "web_search succeeded - 12ms" in out and '"query": "python"' in out


def _execution_kwargs(
    seeded: Seeded, status: ToolStatus, name: str = "web_search"
) -> dict[str, object]:
    return {
        "user_id": seeded.user_id,
        "device_id": seeded.device_id,
        "conversation_id": None,
        "tool_name": name,
        "tool_version": "1",
        "risk_class": str(RiskClass.READ_ONLY),
        "status": status,
        "arguments_redacted": {},
        "arguments_hash": "0" * 64,
        "result_meta": None,
        "error_code": None,
        "duration_ms": 1,
    }


def test_daily_counts_exclude_denied_and_other_users(
    alice: Seeded, bob: Seeded, session_factory: SessionFactory
) -> None:
    async def go(session: AsyncSession) -> dict[str, int]:
        repo = ToolExecutionRepository(session)
        for status in (
            ToolStatus.SUCCEEDED,
            ToolStatus.FAILED,
            ToolStatus.TIMED_OUT,
            ToolStatus.DENIED,
        ):
            await repo.add(**_execution_kwargs(alice, status))  # type: ignore[arg-type]
        await repo.add(**_execution_kwargs(alice, ToolStatus.SUCCEEDED, "other_tool"))  # type: ignore[arg-type]
        await repo.add(**_execution_kwargs(bob, ToolStatus.SUCCEEDED))  # type: ignore[arg-type]
        await session.commit()
        since = datetime.now(UTC) - timedelta(hours=1)
        return await repo.counts_since(alice.user_id, since)

    assert run_db(session_factory, go) == {"web_search": 3, "other_tool": 1}


def test_counts_ignore_executions_before_the_window(
    alice: Seeded, session_factory: SessionFactory
) -> None:
    async def go(session: AsyncSession) -> dict[str, int]:
        repo = ToolExecutionRepository(session)
        await repo.add(**_execution_kwargs(alice, ToolStatus.SUCCEEDED))  # type: ignore[arg-type]
        await session.commit()
        return await repo.counts_since(alice.user_id, datetime.now(UTC) + timedelta(hours=1))

    assert run_db(session_factory, go) == {}


def test_attach_to_message_links_executions(
    alice: Seeded, client_ask: tuple[str, str], session_factory: SessionFactory
) -> None:
    conversation_id, message_id = client_ask

    async def go(session: AsyncSession) -> ToolExecution:
        repo = ToolExecutionRepository(session)
        row = await repo.add(**_execution_kwargs(alice, ToolStatus.SUCCEEDED))  # type: ignore[arg-type]
        await repo.attach_to_message([row.id], uuid.UUID(conversation_id), uuid.UUID(message_id))
        await session.commit()
        await session.refresh(row)
        return row

    row = run_db(session_factory, go)
    assert str(row.conversation_id) == conversation_id and str(row.message_id) == message_id
