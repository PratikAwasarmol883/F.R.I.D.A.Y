import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import IntegrationConnection

ACTIVE = "active"
REVOKED = "revoked"


class IntegrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, user_id: uuid.UUID, provider: str) -> IntegrationConnection | None:
        stmt = select(IntegrationConnection).where(
            IntegrationConnection.user_id == user_id,
            IntegrationConnection.provider == provider,
            IntegrationConnection.status == ACTIVE,
        )
        return await self._session.scalar(stmt)

    async def list_for_user(self, user_id: uuid.UUID) -> list[IntegrationConnection]:
        stmt = (
            select(IntegrationConnection)
            .where(IntegrationConnection.user_id == user_id)
            .order_by(IntegrationConnection.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def revoke(self, user_id: uuid.UUID, provider: str) -> bool:
        """Mark revoked and delete the stored credential."""
        stmt = (
            update(IntegrationConnection)
            .where(
                IntegrationConnection.user_id == user_id,
                IntegrationConnection.provider == provider,
                IntegrationConnection.status == ACTIVE,
            )
            .values(status=REVOKED, credential=None, revoked_at=func.now())
        )
        return bool(getattr(await self._session.execute(stmt), "rowcount", 0))

    async def connect(
        self, user_id: uuid.UUID, provider: str, credential: str
    ) -> IntegrationConnection:
        await self.revoke(user_id, provider)  # one active connection per provider
        connection = IntegrationConnection(
            id=uuid.uuid4(),
            user_id=user_id,
            provider=provider,
            credential=credential,
            status=ACTIVE,
        )
        self._session.add(connection)
        await self._session.flush()
        await self._session.refresh(connection)
        return connection
