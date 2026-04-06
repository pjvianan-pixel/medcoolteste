"""VideoSession model – F3 Part 2: WebRTC video sessions.

One VideoSession is created per ConsultRequest (1:1).  The ``room_id`` maps to a
Twilio Video room.  Status transitions:

    CREATING → READY    (room successfully provisioned)
    READY    → ACTIVE   (first participant joined)
    ACTIVE   → ENDED    (session closed by participant or system)
    *        → ERROR    (any unrecoverable failure)
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class VideoSessionStatus(enum.StrEnum):
    CREATING = "CREATING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    ERROR = "ERROR"


class VideoSession(Base):
    """Tracks a Twilio Video room provisioned for a ConsultRequest.

    Each ConsultRequest may have at most one VideoSession (enforced by the
    unique constraint on ``consult_request_id``).
    """

    __tablename__ = "video_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    consult_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consult_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    room_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fixed to 'TWILIO' for now; extensible to other providers later.
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="TWILIO")
    status: Mapped[VideoSessionStatus] = mapped_column(
        SAEnum(VideoSessionStatus, name="video_session_status"),
        nullable=False,
        default=VideoSessionStatus.CREATING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    consult_request: Mapped["ConsultRequest"] = relationship(  # noqa: F821
        "ConsultRequest", foreign_keys=[consult_request_id]
    )

    __table_args__ = (
        UniqueConstraint("consult_request_id", name="uq_video_sessions_consult_request_id"),
    )

