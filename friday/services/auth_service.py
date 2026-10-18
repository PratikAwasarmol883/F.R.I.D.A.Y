import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from friday.core.security import TOKEN_PREFIX, generate_token, hash_token
from friday.domain.enums import AuditOutcome, DeviceType
from friday.domain.exceptions import NotAuthenticatedError, NotFoundError
from friday.persistence.models import Device, User
from friday.persistence.repositories.audit import AuditRepository
from friday.persistence.repositories.devices import DeviceRepository
from friday.persistence.repositories.users import UserRepository


@dataclass(frozen=True)
class CurrentUser:
    user_id: uuid.UUID
    device_id: uuid.UUID


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._devices = DeviceRepository(session)
        self._audit = AuditRepository(session)

    async def authenticate(self, token: str | None) -> CurrentUser:
        """All failure modes raise the same error so callers cannot probe token state."""
        if not token or not token.startswith(TOKEN_PREFIX):
            raise NotAuthenticatedError
        device = await self._devices.get_active_by_token_hash(hash_token(token))
        if device is None:
            raise NotAuthenticatedError
        await self._devices.touch_last_seen(device.id)
        await self._session.commit()
        return CurrentUser(user_id=device.user_id, device_id=device.id)

    async def create_user(self, display_name: str, timezone: str) -> User:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone: {timezone}") from exc
        user = await self._users.create(display_name, timezone)
        await self._audit.add(
            action="user.create",
            outcome=AuditOutcome.SUCCESS,
            user_id=user.id,
            target_type="user",
            target_id=str(user.id),
        )
        await self._session.commit()
        return user

    async def register_device(
        self, user_id: uuid.UUID, name: str, device_type: DeviceType
    ) -> tuple[Device, str]:
        """Returns the device and the plaintext token; only the hash is persisted."""
        if await self._users.get(user_id) is None:
            raise NotFoundError
        token = generate_token()
        device = await self._devices.create(user_id, name, str(device_type), hash_token(token))
        await self._audit.add(
            action="device.register",
            outcome=AuditOutcome.SUCCESS,
            user_id=user_id,
            device_id=device.id,
            target_type="device",
            target_id=str(device.id),
        )
        await self._session.commit()
        return device, token

    async def revoke_device(self, device_id: uuid.UUID) -> None:
        device = await self._devices.revoke(device_id)
        if device is None:
            raise NotFoundError
        await self._audit.add(
            action="device.revoke",
            outcome=AuditOutcome.SUCCESS,
            user_id=device.user_id,
            device_id=device.id,
            target_type="device",
            target_id=str(device.id),
        )
        await self._session.commit()
