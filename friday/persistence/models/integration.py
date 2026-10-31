import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from friday.persistence.database import Base


class IntegrationConnection(Base):
    """A user's link to an external provider. The credential is encrypted and removed on revoke."""

    __tablename__ = "integration_connections"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="status"),
        Index(
            "uq_integration_connections_user_id_provider_active",
            "user_id",
            "provider",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    credential: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
