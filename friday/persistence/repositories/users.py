import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from friday.persistence.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, display_name: str, timezone: str) -> User:
        user = User(id=uuid.uuid4(), display_name=display_name, timezone=timezone)
        self._session.add(user)
        await self._session.flush()
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)
