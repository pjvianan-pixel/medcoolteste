import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ConsultRequestStatus(enum.StrEnum):
    queued = "queued"
    offering = "offering"
    matched = "matched"
    canceled = "canceled"
    expired = "expired"
    cancelled_by_patient = "cancelled_by_patient"
    cancelled_by_professional = "cancelled_by_professional"
    no_show_patient = "no_show_patient"


class ConsultRequest(Base):
    __tablename__ = "consult_requests"

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
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consult_quotes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    complaint: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[ConsultRequestStatus] = mapped_column(
        SAEnum(ConsultRequestStatus, name="consult_request_status"),
        nullable=False,
        default=ConsultRequestStatus.queued,
    )
    matched_professional_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    no_show_marked_at: Mapped[datetime | None] = mapped_column(
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

    patient: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[patient_user_id]
    )
    matched_professional: Mapped["User | None"] = relationship(  # noqa: F821
        "User", foreign_keys=[matched_professional_user_id]
    )
    quote: Mapped["ConsultQuote"] = relationship("ConsultQuote")  # noqa: F821
    offers: Mapped[list["ConsultOffer"]] = relationship(  # noqa: F821
        "ConsultOffer", back_populates="consult_request", lazy="select"
    )
