"""F3 Part 2 – WebSocket video signalling endpoint.

This endpoint brokers WebRTC signalling messages (SDP offer/answer and ICE
candidates) between the patient and the professional for a given ConsultRequest.
No media is transmitted here; the actual video/audio flows through Twilio Video
(or, in future, directly peer-to-peer once a connection is established).

Authentication & authorisation
-------------------------------
Same pattern as the chat WebSocket:
    ws/video/consults/{consult_id}?token=<jwt>

Wire protocol (JSON)
--------------------
Client → Server:
    {"type": "offer",     "sdp": "<SDP string>"}
    {"type": "answer",    "sdp": "<SDP string>"}
    {"type": "ice",       "candidate": {<RTCIceCandidateInit>}}

Server → Client (relay to the other participant):
    {"type": "offer",     "sdp": "<SDP string>",  "from": "<user_id>"}
    {"type": "answer",    "sdp": "<SDP string>",  "from": "<user_id>"}
    {"type": "ice",       "candidate": {…},        "from": "<user_id>"}

Server → Client (error):
    {"type": "error", "detail": "<reason>"}

The server simply relays signalling messages to the *other* connection(s) in
the same room; it does not interpret SDP or ICE payloads.
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.consult_request import ConsultRequest
from app.db.models.user import User
from app.db.session import AsyncSessionLocal

router = APIRouter(tags=["video"])

_RELAY_EVENT_TYPES = frozenset({"offer", "answer", "ice"})

# ── Session factory (overridable in tests) ────────────────────────────────────

_ws_session_factory = AsyncSessionLocal


def _get_ws_session():
    """Return an async context manager for a DB session (allows test override)."""
    return _ws_session_factory()


# ── Connection manager ────────────────────────────────────────────────────────


class _VideoConnectionManager:
    """Tracks active WebSocket connections for video signalling rooms."""

    def __init__(self) -> None:
        # { consult_id: [WebSocket, ...] }
        self._rooms: dict[uuid.UUID, list[WebSocket]] = {}

    def connect(self, consult_id: uuid.UUID, ws: WebSocket) -> None:
        self._rooms.setdefault(consult_id, []).append(ws)

    def disconnect(self, consult_id: uuid.UUID, ws: WebSocket) -> None:
        room = self._rooms.get(consult_id, [])
        if ws in room:
            room.remove(ws)
        if not room:
            self._rooms.pop(consult_id, None)

    async def relay(
        self,
        consult_id: uuid.UUID,
        sender: WebSocket,
        payload: dict[str, Any],
    ) -> None:
        """Forward *payload* to every connection in the room except *sender*."""
        data = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._rooms.get(consult_id, [])):
            if ws is sender:
                continue
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(consult_id, ws)


_manager = _VideoConnectionManager()


# ── Auth helpers ──────────────────────────────────────────────────────────────


async def _authenticate_ws(token: str, db: AsyncSession) -> User | None:
    """Validate a JWT token and return the corresponding active User."""
    try:
        user_id = decode_access_token(token)
    except JWTError:
        return None
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def _authorise_participant(
    db: AsyncSession,
    consult_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Return True if *user_id* is the patient or professional for *consult_id*."""
    result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == consult_id)
    )
    consult = result.scalar_one_or_none()
    if consult is None:
        return False
    return consult.patient_user_id == user_id or consult.matched_professional_user_id == user_id


# ── WebSocket endpoint ────────────────────────────────────────────────────────


@router.websocket("/ws/video/consults/{consult_id}")
async def video_signalling_websocket(
    websocket: WebSocket,
    consult_id: uuid.UUID,
    token: str = "",
) -> None:
    """WebRTC signalling channel for a ConsultRequest.

    Connection lifecycle:
    1. Accept the WebSocket connection.
    2. Authenticate the caller via ``?token=<jwt>``.
    3. Verify the caller is the patient or professional for *this* consult.
    4. Join the signalling room and relay offer/answer/ICE messages to the
       other participant until the client disconnects.
    """
    await websocket.accept()

    # ── Authentication ──────────────────────────────────────────────────
    if not token:
        await websocket.send_text(
            json.dumps({"type": "error", "detail": "Missing authentication token"})
        )
        await websocket.close(code=4001)
        return

    async with _get_ws_session() as db:
        user = await _authenticate_ws(token, db)
        if user is None:
            await websocket.send_text(
                json.dumps({"type": "error", "detail": "Invalid or expired token"})
            )
            await websocket.close(code=4001)
            return

        # ── Authorisation ───────────────────────────────────────────────
        is_participant = await _authorise_participant(db, consult_id, user.id)
        if not is_participant:
            await websocket.send_text(
                json.dumps(
                    {"type": "error", "detail": "Not a participant of this consult request"}
                )
            )
            await websocket.close(code=4003)
            return

    # ── Join room ────────────────────────────────────────────────────────
    _manager.connect(consult_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "Invalid JSON"})
                )
                continue

            event_type = data.get("type")
            if event_type not in _RELAY_EVENT_TYPES:
                await websocket.send_text(
                    json.dumps(
                        {"type": "error", "detail": f"Unknown event type: {event_type}"}
                    )
                )
                continue

            # Relay the message to the other participant(s), annotated with sender.
            relay_payload: dict[str, Any] = {**data, "from": str(user.id)}
            await _manager.relay(consult_id, websocket, relay_payload)

    except WebSocketDisconnect:
        _manager.disconnect(consult_id, websocket)
