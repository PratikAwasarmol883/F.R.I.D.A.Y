from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friday.domain.exceptions import DeviceRevokedError
from friday.persistence.repositories.devices import DeviceRepository
from friday.services.auth_service import CurrentUser
from friday.workers.context import TaskContext


async def load_current_user(
    ctx: TaskContext, session_factory: async_sessionmaker[AsyncSession]
) -> CurrentUser:
    """Tasks run with the creating device's *current* state, never a snapshot."""
    async with session_factory() as session:
        device = await DeviceRepository(session).get(ctx.device_id) if ctx.device_id else None
    if device is None or device.revoked_at is not None or device.user_id != ctx.user_id:
        raise DeviceRevokedError
    return CurrentUser(user_id=ctx.user_id, device_id=device.id, scopes=frozenset(device.scopes))
