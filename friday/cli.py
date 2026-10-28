"""Operator CLI. Requires server-side shell access; there is no public signup endpoint."""

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.core.request_context import request_id_var
from friday.domain.enums import DeviceType
from friday.domain.exceptions import DomainError
from friday.domain.task_state import InvalidTransitionError, TaskStatus
from friday.persistence.database import get_engine, get_sessionmaker
from friday.persistence.repositories.tasks import TaskRepository
from friday.persistence.repositories.tool_executions import ToolExecutionRepository
from friday.services.auth_service import AuthService


def _split_scopes(raw: str) -> list[str]:
    return [part for part in raw.split(",") if part.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="friday")
    sub = parser.add_subparsers(dest="command", required=True)

    create_user = sub.add_parser("create-user", help="Create a user")
    create_user.add_argument("--name", required=True)
    create_user.add_argument("--timezone", default="UTC")

    register = sub.add_parser("register-device", help="Register a device and print its token once")
    register.add_argument("--user-id", type=uuid.UUID, required=True)
    register.add_argument("--name", required=True)
    register.add_argument("--type", type=DeviceType, choices=list(DeviceType), required=True)
    register.add_argument("--scopes", default="", help="Comma-separated, e.g. tool:web_search")

    set_scopes = sub.add_parser("set-device-scopes", help="Replace a device's scopes")
    set_scopes.add_argument("--device-id", type=uuid.UUID, required=True)
    set_scopes.add_argument("--scopes", default="", help="Comma-separated; empty clears all")

    tasks = sub.add_parser("tasks", help="List a user's recent tasks (no input text)")
    tasks.add_argument("--user-id", type=uuid.UUID, required=True)
    tasks.add_argument("--status", type=TaskStatus, choices=list(TaskStatus))
    tasks.add_argument("--limit", type=int, default=20)

    requeue = sub.add_parser("requeue-task", help="Re-run a failed or timed-out task")
    requeue.add_argument("--task-id", type=uuid.UUID, required=True)

    tool_log = sub.add_parser("tool-log", help="Show recent tool executions for a user")
    tool_log.add_argument("--user-id", type=uuid.UUID, required=True)
    tool_log.add_argument("--limit", type=int, default=20)

    revoke = sub.add_parser("revoke-device", help="Revoke a device immediately")
    revoke.add_argument("--device-id", type=uuid.UUID, required=True)
    return parser


async def _run(args: argparse.Namespace, session: AsyncSession) -> None:
    auth = AuthService(session)
    if args.command == "create-user":
        user = await auth.create_user(args.name, args.timezone)
        print(f"user_id={user.id}")
    elif args.command == "register-device":
        device, token = await auth.register_device(
            args.user_id, args.name, args.type, _split_scopes(args.scopes)
        )
        print(f"device_id={device.id}")
        print(f"token={token}")
        print("Store this token now; it cannot be shown again.", file=sys.stderr)
    elif args.command == "set-device-scopes":
        device = await auth.set_device_scopes(args.device_id, _split_scopes(args.scopes))
        print(f"scopes={','.join(device.scopes)}")
    elif args.command == "tasks":
        rows = await TaskRepository(session).list_for_user(
            args.user_id, args.status, max(1, min(args.limit, 200))
        )
        for task in rows:
            print(
                f"{task.id} {task.type} {task.status} attempt={task.attempt}/{task.max_attempts} "
                f"error={task.error_code or '-'} created={task.created_at.isoformat()}"
            )
    elif args.command == "requeue-task":
        task = await TaskRepository(session).requeue_by_operator(args.task_id)
        await session.commit()
        print(f"requeued={task.id}")
    elif args.command == "tool-log":
        for row in await ToolExecutionRepository(session).recent_for_user(
            args.user_id, max(1, min(args.limit, 200))
        ):
            print(
                f"{row.created_at.isoformat()} {row.tool_name} {row.status} "
                f"{row.error_code or '-'} {row.duration_ms}ms {json.dumps(row.arguments_redacted)}"
            )
    else:
        await auth.revoke_device(args.device_id)
        print("revoked")


def main(
    argv: list[str] | None = None, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> int:
    args = _build_parser().parse_args(argv)
    request_id_var.set(uuid.uuid4().hex)
    owns_engine = session_factory is None
    factory = session_factory or get_sessionmaker()

    async def runner() -> None:
        try:
            async with factory() as session:
                await _run(args, session)
        finally:
            if owns_engine:
                await get_engine().dispose()

    try:
        asyncio.run(runner())
    except (DomainError, ValueError, InvalidTransitionError) as exc:
        print(f"error: {type(exc).__name__}: {exc}".rstrip(": "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
