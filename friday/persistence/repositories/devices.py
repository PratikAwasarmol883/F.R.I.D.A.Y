import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import Device

LAST_SEEN_THROTTLE = timedelta(minutes=5)


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, user_id: uuid.UUID, name: str, device_type: str, token_hash: str
    ) -> Device:
        device = Device(
            id=uuid.uuid4(),
            user_id=user_id,
            name=name,
            device_type=device_type,
            token_hash=token_hash,
        )
        self._session.add(device)
        await self._session.flush()
        return device

    async def get_active_by_token_hash(self, token_hash: str) -> Device | None:
        stmt = select(Device).where(Device.token_hash == token_hash, Device.revoked_at.is_(None))
        return await self._session.scalar(stmt)

    async def touch_last_seen(self, device_id: uuid.UUID) -> None:
        """Single atomic UPDATE so concurrent requests cannot amplify writes."""
        now = datetime.now(UTC)
        stmt = (
            update(Device)
            .where(
                Device.id == device_id,
                or_(Device.last_seen_at.is_(None), Device.last_seen_at < now - LAST_SEEN_THROTTLE),
            )
            .values(last_seen_at=now)
        )
        await self._session.execute(stmt)

    async def revoke(self, device_id: uuid.UUID) -> Device | None:
        """Revoke an active device; returns None if it is missing or already revoked."""
        stmt = (
            update(Device)
            .where(Device.id == device_id, Device.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
            .returning(Device)
        )
        return await self._session.scalar(stmt)
