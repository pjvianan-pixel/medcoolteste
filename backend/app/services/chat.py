"""Service layer for F3 Part 1 – Real-time Chat.

All business logic (ownership checks, status validation, message persistence,
and history retrieval) lives here so that both the REST routers and the
WebSocket handler stay thin.

Future extension points
-----------------------
- ``MessageType.SYSTEM``:  server-generated events (call started/ended, etc.)
- ``MessageType.FILE``:    attachment messages with storage URL.
- Typing indicators / presence: add a separate Redis-backed channel (F3 Part 2).
- Read receipts: add a ``mark_messages_read`` helper and call it from the WS
  handler when the client sends a ``{"type": "read"}`` event.
"""

import uuid
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_message import ChatMessage, MessageType, SenderRole
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.user import UserRole

# ConsultRequest statuses that allow chat.
_CHAT_ALLOWED_STATUSES: frozenset[ConsultRequestStatus] = frozenset(
    {
        ConsultRequestStatus.matched,
        ConsultRequestStatus.no_show_patient,
        # Keeping no_show_patient so parties can still communicate after a
        # no-show has been recorded (e.g. to reschedule).
    }
)


async def _load_and_authorise_consult(
    db: AsyncSession,
    consult_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[ConsultRequest, SenderRole]:
    """Load the ConsultRequest and verify the user is a participant.

    Returns ``(consult, sender_role)`` where ``sender_role`` is PATIENT or
    PROFESSIONAL depending on which side the caller is.

    Raises:
        404 – consult not found.
        403 – user is not the patient or professional for this consult.
    """
    result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == consult_id)
    )
    consult = result.scalar_one_or_none()
    if consult is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consult request not found",
        )
    if consult.patient_user_id == user_id:
        return consult, SenderRole.PATIENT
    if consult.matched_professional_user_id == user_id:
        return consult, SenderRole.PROFESSIONAL
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not a participant in this consult request",
    )


async def send_chat_message(
    db: AsyncSession,
    consult_id: uuid.UUID,
    sender_user_id: uuid.UUID,
    content: str,
) -> ChatMessage:
    """Persist a new chat message and return the saved ORM object.

    Validates:
      - Consult exists and the sender is a participant.
      - Consult is in a status that allows chat.

    The caller is responsible for broadcasting the result over WebSocket.

    Future: ``message_type`` will be extended when FILE / SYSTEM messages land.
    """
    consult, sender_role = await _load_and_authorise_consult(
        db, consult_id, sender_user_id
    )

    if consult.status not in _CHAT_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Chat is not available for a consult in '{consult.status}' status. "
                f"Allowed statuses: {[s.value for s in _CHAT_ALLOWED_STATUSES]}"
            ),
        )

    # Determine the receiver (the other side).
    if sender_role == SenderRole.PATIENT:
        receiver_user_id = consult.matched_professional_user_id
    else:
        receiver_user_id = consult.patient_user_id

    if receiver_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No matched professional found for this consult request",
        )

    msg = ChatMessage(
        id=uuid.uuid4(),
        consult_request_id=consult.id,
        sender_user_id=sender_user_id,
        receiver_user_id=receiver_user_id,
        sender_role=sender_role,
        message_type=MessageType.TEXT,
        content=content,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def list_chat_messages(
    db: AsyncSession,
    consult_id: uuid.UUID,
    user_id: uuid.UUID,
    page: int = 1,
    limit: int = 50,
    before: datetime | None = None,
    after: datetime | None = None,
) -> tuple[list[ChatMessage], int]:
    """Return paginated chat messages for a consult, ordered by ``sent_at``.

    ``before`` / ``after`` support infinite-scroll / cursor pagination on the
    ``sent_at`` field in addition to page-based pagination.

    Returns ``(messages, total_count)``.

    Raises:
        404 – consult not found.
        403 – user is not a participant.
    """
    await _load_and_authorise_consult(db, consult_id, user_id)

    base_where = [ChatMessage.consult_request_id == consult_id]
    if before is not None:
        base_where.append(ChatMessage.sent_at < before)
    if after is not None:
        base_where.append(ChatMessage.sent_at > after)

    # Total count (respects before/after filters).
    count_result = await db.execute(
        select(func.count()).select_from(ChatMessage).where(*base_where)
    )
    total = count_result.scalar_one()

    offset = (page - 1) * limit
    rows_result = await db.execute(
        select(ChatMessage)
        .where(*base_where)
        .order_by(ChatMessage.sent_at)
        .offset(offset)
        .limit(limit)
    )
    messages = list(rows_result.scalars().all())
    return messages, total


async def get_sender_role_for_consult(
    db: AsyncSession,
    consult_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SenderRole:
    """Return the role of the user in the consult without status validation.

    Used by the WebSocket handler to determine sender_role before committing
    to a connection.  Raises 403/404 on invalid access.
    """
    _, role = await _load_and_authorise_consult(db, consult_id, user_id)
    return role
