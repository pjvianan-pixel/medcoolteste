import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class PaymentStatus(enum.StrEnum):
    pending = "pending"
    processing = "processing"
    paid = "paid"
    refund_pending = "refund_pending"
    refunded = "refunded"
    failed = "failed"
    canceled = "canceled"


class PaymentEventType(enum.StrEnum):
    created = "created"
    status_changed = "status_changed"
    provider_callback = "provider_callback"
    refund_requested = "refund_requested"
    refund_completed = "refund_completed"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    consult_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consult_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    professional_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="BRL")
    platform_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    professional_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    payout_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("professional_payouts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status"),
        nullable=False,
        default=PaymentStatus.pending,
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
        "ConsultRequest"
    )
    patient: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[patient_user_id]
    )
    professional: Mapped["User | None"] = relationship(  # noqa: F821
        "User", foreign_keys=[professional_user_id]
    )
    events: Mapped[list["PaymentEvent"]] = relationship(
        "PaymentEvent", back_populates="payment", lazy="select"
    )
    payout: Mapped["ProfessionalPayout | None"] = relationship(  # noqa: F821
        "ProfessionalPayout", back_populates="payments", foreign_keys=[payout_id]
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[PaymentEventType] = mapped_column(
        SAEnum(PaymentEventType, name="payment_event_type"),
        nullable=False,
    )
    gateway_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    payment: Mapped["Payment"] = relationship("Payment", back_populates="events")
