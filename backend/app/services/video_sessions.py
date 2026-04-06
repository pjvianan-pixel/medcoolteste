"""Video session service – F3 Part 2.

Manages the lifecycle of VideoSession records and orchestrates calls to the
Twilio Video stub (``app.services.twilio_video``).

Access rules
------------
* Only the **matched professional** of a ConsultRequest may create a video
  session.
* Both the **patient** and the **professional** may read or end the session.
* The ConsultRequest must be in ``matched`` status for video to be available.
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.video_session import VideoSession, VideoSessionStatus
from app.services.twilio_video import create_video_room, delete_video_room

_VIDEO_ALLOWED_STATUSES = frozenset(
    {
        ConsultRequestStatus.matched,
    }
)


async def _load_consult_and_authorise(
    db: AsyncSession,
    consult_request_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConsultRequest:
    """Load a ConsultRequest and verify *user_id* is a participant.

    Returns the ConsultRequest on success.
    Raises HTTP 404 if not found, 403 if user is not a participant.
    """
    result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == consult_request_id)
    )
    consult = result.scalar_one_or_none()
    if consult is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consult request not found",
        )
    is_patient = consult.patient_user_id == user_id
    is_professional = consult.matched_professional_user_id == user_id
    if not (is_patient or is_professional):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a participant of this consult request",
        )
    return consult


async def create_video_session(
    db: AsyncSession,
    consult_request_id: uuid.UUID,
    professional_user_id: uuid.UUID,
) -> VideoSession:
    """Provision a new VideoSession for a ConsultRequest.

    Only the matched professional may call this endpoint.  Raises HTTP 409 if a
    session already exists, HTTP 422 if the consult is not in an allowed status.
    """
    result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == consult_request_id)
    )
    consult = result.scalar_one_or_none()
    if consult is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consult request not found",
        )
    if consult.matched_professional_user_id != professional_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the matched professional may create a video session",
        )
    if consult.status not in _VIDEO_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Video sessions can only be created for consults in status "
                f"{[s.value for s in _VIDEO_ALLOWED_STATUSES]}. "
                f"Current status: {consult.status.value}"
            ),
        )

    # Check for an existing session (idempotency guard).
    existing_result = await db.execute(
        select(VideoSession).where(VideoSession.consult_request_id == consult_request_id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A video session already exists for this consult request",
        )

    # Provision the room via Twilio (stub).
    room_info = create_video_room(consult_request_id)

    session = VideoSession(
        id=uuid.uuid4(),
        consult_request_id=consult_request_id,
        room_id=room_info.room_id,
        provider="TWILIO",
        status=VideoSessionStatus.READY,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_video_session(
    db: AsyncSession,
    consult_request_id: uuid.UUID,
    user_id: uuid.UUID,
) -> VideoSession | None:
    """Return the VideoSession for *consult_request_id*, or ``None`` if none exists.

    Verifies the caller is a participant; raises HTTP 403/404 otherwise.
    """
    await _load_consult_and_authorise(db, consult_request_id, user_id)

    result = await db.execute(
        select(VideoSession).where(VideoSession.consult_request_id == consult_request_id)
    )
    return result.scalar_one_or_none()


async def end_video_session(
    db: AsyncSession,
    consult_request_id: uuid.UUID,
    user_id: uuid.UUID,
) -> VideoSession:
    """Mark a VideoSession as ENDED.

    Either participant (patient or professional) may end the session.
    Raises HTTP 404 if no session exists, HTTP 422 if already ended.
    """
    await _load_consult_and_authorise(db, consult_request_id, user_id)

    result = await db.execute(
        select(VideoSession).where(VideoSession.consult_request_id == consult_request_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No video session found for this consult request",
        )
    if session.status == VideoSessionStatus.ENDED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Video session is already ended",
        )

    session.status = VideoSessionStatus.ENDED
    session.ended_at = datetime.now(UTC)

    # Attempt to clean up the Twilio room (best-effort; stub for now).
    try:
        delete_video_room(session.room_id)
    except Exception:  # noqa: BLE001
        pass

    await db.commit()
    await db.refresh(session)
    return session
