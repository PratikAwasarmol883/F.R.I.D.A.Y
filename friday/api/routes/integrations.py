from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.schemas import IntegrationOut, NtfyConnect
from friday.core.config import Settings, get_settings
from friday.persistence.database import get_db_session
from friday.persistence.models import IntegrationConnection
from friday.services.auth_service import CurrentUser
from friday.services.integration_service import IntegrationService

router = APIRouter(prefix="/integrations", tags=["integrations"])


def to_integration_out(connection: IntegrationConnection) -> IntegrationOut:
    return IntegrationOut(  # the credential is never part of any response
        provider=connection.provider,
        status=connection.status,
        created_at=connection.created_at,
        revoked_at=connection.revoked_at,
    )


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[IntegrationOut]:
    rows = await IntegrationService(session, settings).list_for_user(user)
    return [to_integration_out(c) for c in rows]


@router.post(
    "/{provider}/connect", response_model=IntegrationOut, status_code=status.HTTP_201_CREATED
)
async def connect_integration(
    provider: str,
    body: NtfyConnect,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> IntegrationOut:
    connection = await IntegrationService(session, settings).connect(user, provider, body.topic)
    return to_integration_out(connection)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_integration(
    provider: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    await IntegrationService(session, settings).revoke(user, provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
