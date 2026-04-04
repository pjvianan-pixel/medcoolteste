import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class QuoteStatus(enum.StrEnum):
    active = "active"
    expired = "expired"
    used = "used"


class ConsultQuote(Base):
    __tablename__ = "consult_quotes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    specialty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("specialties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quoted_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[QuoteStatus] = mapped_column(
        SAEnum(QuoteStatus, name="quote_status"),
        nullable=False,
        default=QuoteStatus.active,
    )

    patient: Mapped["User"] = relationship("User", foreign_keys=[patient_user_id])  # noqa: F821
    specialty: Mapped["Specialty"] = relationship("Specialty")  # noqa: F821
