import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from friday.persistence.database import Base


class Memory(Base):
    """A user-approved (or pending) note. Deleted for real, never soft-deleted."""

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("kind IN ('preference', 'fact', 'project', 'note')", name="kind"),
        CheckConstraint("status IN ('pending', 'approved')", name="status"),
        CheckConstraint("source IN ('user', 'assistant')", name="source"),
        Index("ix_memories_user_id_status_updated_at", "user_id", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
