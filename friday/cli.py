"""Operator CLI. Requires server-side shell access; there is no public signup endpoint."""

import argparse
import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.core.request_context import request_id_var
from friday.domain.enums import DeviceType
from friday.domain.exceptions import DomainError
from friday.persistence.database import get_engine, get_sessionmaker
from friday.services.auth_service import AuthService


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

    revoke = sub.add_parser("revoke-device", help="Revoke a device immediately")
    revoke.add_argument("--device-id", type=uuid.UUID, required=True)
    return parser


async def _run(args: argparse.Namespace, session: AsyncSession) -> None:
    auth = AuthService(session)
    if args.command == "create-user":
        user = await auth.create_user(args.name, args.timezone)
        print(f"user_id={user.id}")
    elif args.command == "register-device":
        device, token = await auth.register_device(args.user_id, args.name, args.type)
        print(f"device_id={device.id}")
        print(f"token={token}")
        print("Store this token now; it cannot be shown again.", file=sys.stderr)
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
    except (DomainError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}".rstrip(": "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
