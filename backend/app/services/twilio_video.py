"""Twilio Video integration – F3 Part 3.

This module wraps Twilio Video room lifecycle operations.

When ``TWILIO_ACCOUNT_SID``, ``TWILIO_API_KEY``, and ``TWILIO_API_SECRET`` are
all set in the environment the module uses the real Twilio SDK.  If any of
those variables is missing it falls back to deterministic stub values so the
rest of the system can be developed and tested without real credentials.

Environment variables (add to ``.env``):

.. code-block:: ini

    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_API_KEY=SKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_API_SECRET=<your-api-secret>
    TWILIO_VIDEO_ROOM_PREFIX=medcool-   # optional, default shown
"""

import uuid
from dataclasses import dataclass

from app.core.config import settings


@dataclass
class VideoRoomInfo:
    """Information returned after provisioning a Twilio Video room."""

    room_id: str
    room_url: str
    token: str


def _credentials_configured() -> bool:
    """Return True when all required Twilio credentials are present."""
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_API_KEY
        and settings.TWILIO_API_SECRET
    )


def create_video_room(consult_request_id: uuid.UUID, user_id: uuid.UUID) -> VideoRoomInfo:
    """Provision a Twilio Video room and return an access token for *user_id*.

    Uses the real Twilio SDK when credentials are configured; otherwise returns
    deterministic stub values suitable for local development and testing.
    """
    room_name = f"{settings.TWILIO_VIDEO_ROOM_PREFIX}consult-{consult_request_id}"

    if _credentials_configured():
        from twilio.jwt.access_token import AccessToken  # noqa: PLC0415
        from twilio.jwt.access_token.grants import VideoGrant  # noqa: PLC0415
        from twilio.rest import Client  # noqa: PLC0415

        client = Client(
            settings.TWILIO_API_KEY,
            settings.TWILIO_API_SECRET,
            account_sid=settings.TWILIO_ACCOUNT_SID,
        )
        room = client.video.rooms.create(unique_name=room_name)
        token = AccessToken(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_API_KEY,
            settings.TWILIO_API_SECRET,
            identity=str(user_id),
        )
        token.add_grant(VideoGrant(room=room_name))
        return VideoRoomInfo(
            room_id=room.sid,
            room_url=f"https://video.twilio.com/rooms/{room.sid}",
            token=token.to_jwt(),
        )

    # Stub fallback – no real Twilio credentials configured.
    return VideoRoomInfo(
        room_id=room_name,
        room_url=f"https://video.twilio.com/rooms/{room_name}",
        token=f"stub-jwt-{user_id}-room-{consult_request_id}",
    )


def generate_access_token(user_id: uuid.UUID, room_name: str) -> str:
    """Generate a Twilio Video access token for *user_id* in *room_name*.

    Used by GET endpoints so each participant receives a fresh token for an
    already-provisioned room.  Falls back to a stub token when Twilio
    credentials are not configured.
    """
    if _credentials_configured():
        from twilio.jwt.access_token import AccessToken  # noqa: PLC0415
        from twilio.jwt.access_token.grants import VideoGrant  # noqa: PLC0415

        token = AccessToken(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_API_KEY,
            settings.TWILIO_API_SECRET,
            identity=str(user_id),
        )
        token.add_grant(VideoGrant(room=room_name))
        return token.to_jwt()

    return f"stub-jwt-{user_id}-room-{room_name}"


def delete_video_room(room_id: str) -> None:
    """Terminate a Twilio Video room by setting its status to ``completed``.

    Uses the real Twilio SDK when credentials are configured; otherwise a
    no-op stub.
    """
    if _credentials_configured():
        from twilio.rest import Client  # noqa: PLC0415

        client = Client(
            settings.TWILIO_API_KEY,
            settings.TWILIO_API_SECRET,
            account_sid=settings.TWILIO_ACCOUNT_SID,
        )
        client.video.rooms(room_id).update(status="completed")  # type: ignore[arg-type]
