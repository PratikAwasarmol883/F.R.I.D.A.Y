import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from friday.persistence.database import Base


class ToolExecution(Base):
    """One row per model-proposed tool call, including denied and failed ones.

    Stores redacted arguments and result metadata only; never full result bodies.
    """

    __tablename__ = "tool_executions"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded', 'failed', 'denied', 'timed_out')", name="status"),
        CheckConstraint(
            "risk_class IN ('read_only', 'reversible_write', 'consequential', 'destructive')",
            name="risk_class",
        ),
        Index("ix_tool_executions_user_id_created_at", "user_id", "created_at"),
        Index("ix_tool_executions_tool_name_created_at", "tool_name", "created_at"),
        Index("ix_tool_executions_conversation_id", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_version: Mapped[str] = mapped_column(String(16))
    risk_class: Mapped[str | None] = mapped_column(String(24))  # NULL for unregistered names
    status: Mapped[str] = mapped_column(String(16))
    arguments_redacted: Mapped[dict[str, Any]] = mapped_column(JSONB)
    arguments_hash: Mapped[str] = mapped_column(String(64))
    result_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    approval_id: Mapped[uuid.UUID | None] = mapped_column()  # reserved for the approval flow
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
