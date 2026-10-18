import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.schemas import ConversationCreate, ConversationDetail, ConversationOut, MessageOut
from friday.persistence.database import get_db_session
from friday.services.auth_service import CurrentUser
from friday.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationOut:
    conversation = await ConversationService(session).create(user, body.title)
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=200),
    before_sequence: int | None = Query(default=None, ge=1),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ConversationDetail:
    page = await ConversationService(session).get_page(
        user, conversation_id, limit, before_sequence
    )
    return ConversationDetail(
        **ConversationOut.model_validate(page.conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in page.messages],
        next_before_sequence=page.next_before_sequence,
    )
