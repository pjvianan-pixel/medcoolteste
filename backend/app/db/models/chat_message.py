"""F3 Part 1 – Chat domain model.

ChatMessage persists every message exchanged between a patient and a
professional inside a matched ConsultRequest.

Future extension points (F3 Part 2+):
  - MessageType.SYSTEM  – server-generated events (e.g. "call started").
  - MessageType.FILE    – attachment references (images, documents).
  - ``delivered_at`` / ``read_at`` – delivery/read receipts (UI indicators).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class MessageType(enum.StrEnum):
    TEXT = "TEXT"
    # TODO (F3 Part 2): SYSTEM = "SYSTEM"  – server-generated events
    # TODO (F3 Part 2): FILE   = "FILE"    – attachment messages


class SenderRole(enum.StrEnum):
    PATIENT = "PATIENT"
    PROFESSIONAL = "PROFESSIONAL"


class ChatMessage(Base):
    """One chat message within a ConsultRequest conversation.

    The composite index on ``(consult_request_id, sent_at)`` supports efficient
    history pagination (before/after cursor queries).
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    consult_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consult_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    receiver_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_role: Mapped[SenderRole] = mapped_column(
        SAEnum(SenderRole, name="chat_sender_role"),
        nullable=False,
    )
    message_type: Mapped[MessageType] = mapped_column(
        SAEnum(MessageType, name="chat_message_type"),
        nullable=False,
        default=MessageType.TEXT,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # TODO (F3 Part 2): populate via WebSocket delivery acknowledgement.
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # TODO (F3 Part 2): populate when the receiver marks the message as read.
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    consult_request: Mapped["ConsultRequest"] = relationship(  # noqa: F821
        "ConsultRequest", foreign_keys=[consult_request_id]
    )
    sender: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[sender_user_id]
    )
    receiver: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[receiver_user_id]
    )

    __table_args__ = (
        # Composite index for efficient history pagination ordered by time.
        Index("ix_chat_messages_consult_sent", "consult_request_id", "sent_at"),
    )
