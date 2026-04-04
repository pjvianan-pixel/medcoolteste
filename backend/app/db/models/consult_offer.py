import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ConsultOfferStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    expired = "expired"


class ConsultOffer(Base):
    __tablename__ = "consult_offers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    consult_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consult_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    professional_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ConsultOfferStatus] = mapped_column(
        SAEnum(ConsultOfferStatus, name="consult_offer_status"),
        nullable=False,
        default=ConsultOfferStatus.pending,
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    consult_request: Mapped["ConsultRequest"] = relationship(  # noqa: F821
        "ConsultRequest", back_populates="offers"
    )
    professional: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[professional_user_id]
    )
