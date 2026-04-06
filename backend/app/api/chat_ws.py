"""F3 Part 1 – WebSocket chat endpoint.

Each ConsultRequest gets a single WebSocket "room".  When a client connects,
it is authenticated, verified as a participant of the requested consult, and
then added to that room.  Messages sent by one side are broadcast to all
connections in the same room (typically two: patient + professional).

Authentication
--------------
WebSocket clients pass the JWT access token via a ``token`` query parameter:

    ws/chat/consults/{consult_id}?token=<jwt>

TODO (F3 Part 2): migrate to a more secure mechanism (e.g. a short-lived
ticket/one-time token issued by a REST endpoint) to avoid exposing the long-
lived JWT in server logs.

Wire protocol (JSON)
--------------------
Client → Server:
    {"type": "message", "content": "hello", "client_message_id": "<uuid>"}

Server → Client (on new message):
    {"type": "message", "message": <ChatMessageResponse>, "client_message_id": "<echo>"}

Server → Client (on error):
    {"type": "error", "detail": "<reason>"}

Future events (F3 Part 2+):
    {"type": "typing"}          – typing indicator from client
    {"type": "read"}            – mark messages as read
    {"type": "delivered", ...}  – delivery acknowledgement
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.session import AsyncSessionLocal
from app.schemas.schemas import ChatMessageResponse
from app.services.chat import get_sender_role_for_consult, send_chat_message

router = APIRouter(tags=["chat"])

# ── Session factory (overridable in tests) ────────────────────────────────────

# ``_ws_session_factory`` is a callable that returns an async context manager
# yielding an AsyncSession.  Tests can replace this to inject the test session.
# In production it uses the real AsyncSessionLocal.
_ws_session_factory = AsyncSessionLocal


def _get_ws_session():
    """Return an async context manager for a DB session (allows test override)."""
    return _ws_session_factory()


# ── Connection manager ────────────────────────────────────────────────────────


class _ConnectionManager:
    """Keeps track of active WebSocket connections keyed by consult_id.

    Future (F3 Part 2): replace with a Redis pub/sub backend so that multiple
    API server replicas can broadcast to each other's connections.
    """

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

    async def broadcast(self, consult_id: uuid.UUID, payload: dict[str, Any]) -> None:
        """Send ``payload`` to every connection in the room."""
        data = json.dumps(payload, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._rooms.get(consult_id, [])):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(consult_id, ws)


_manager = _ConnectionManager()


# ── Auth helper ───────────────────────────────────────────────────────────────


async def _authenticate_ws(token: str, db: AsyncSession) -> User | None:
    """Validate a JWT token and return the corresponding active User.

    Returns ``None`` if the token is invalid or the user is not found/active.
    """
    try:
        user_id = decode_access_token(token)
    except JWTError:
        return None
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


# ── WebSocket endpoint ────────────────────────────────────────────────────────


@router.websocket("/ws/chat/consults/{consult_id}")
async def chat_websocket(
    websocket: WebSocket,
    consult_id: uuid.UUID,
    token: str = "",
) -> None:
    """Real-time chat channel for a ConsultRequest.

    Connection lifecycle:
    1. Accept the WebSocket connection.
    2. Authenticate the caller via ``?token=<jwt>``.
    3. Verify the caller is the patient or professional for *this* consult.
    4. Join the room and relay messages until the client disconnects.

    The endpoint deliberately closes the connection with a descriptive error
    message (instead of raising an HTTP exception) because WebSocket clients
    cannot receive HTTP 4xx responses after the upgrade handshake.
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
        try:
            await get_sender_role_for_consult(db, consult_id, user.id)
        except Exception:  # noqa: BLE001 – HTTPException from service layer
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
            if event_type != "message":
                # TODO (F3 Part 2): handle "typing", "read", "delivered" events.
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": f"Unknown event type: {event_type}"})
                )
                continue

            content = data.get("content", "").strip()
            if not content:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": "Message content cannot be empty"})
                )
                continue

            client_message_id = data.get("client_message_id")

            # Persist and broadcast
            async with _get_ws_session() as db:
                try:
                    msg = await send_chat_message(
                        db=db,
                        consult_id=consult_id,
                        sender_user_id=user.id,
                        content=content,
                    )
                except Exception as exc:  # noqa: BLE001
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": str(exc)})
                    )
                    continue

            msg_response = ChatMessageResponse.model_validate(msg)
            await _manager.broadcast(
                consult_id,
                {
                    "type": "message",
                    "message": msg_response.model_dump(mode="json"),
                    "client_message_id": client_message_id,
                },
            )

    except WebSocketDisconnect:
        _manager.disconnect(consult_id, websocket)

