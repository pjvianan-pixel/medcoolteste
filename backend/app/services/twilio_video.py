"""Twilio Video integration – F3 Part 2.

This module wraps Twilio Video room lifecycle operations.  In the current phase
the calls are **stubs** that return deterministic mock values so the rest of the
system can be developed and tested without real Twilio credentials.

To activate real Twilio calls:
  1. ``pip install twilio``
  2. Set TWILIO_ACCOUNT_SID, TWILIO_API_KEY, TWILIO_API_SECRET in your .env.
  3. Replace the stub body in ``create_video_room`` with:

     .. code-block:: python

         from twilio.rest import Client
         from twilio.jwt.access_token import AccessToken
         from twilio.jwt.access_token.grants import VideoGrant

         client = Client(settings.TWILIO_API_KEY, settings.TWILIO_API_SECRET,
                         account_sid=settings.TWILIO_ACCOUNT_SID)
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


def create_video_room(consult_request_id: uuid.UUID) -> VideoRoomInfo:
    """Provision (or retrieve) a Twilio Video room for a consult.

    **Current implementation**: stub that returns deterministic mock values.
    See module docstring for instructions on replacing with real Twilio calls.

    TODO: Install ``pip install twilio`` and implement:
        ``client.video.rooms.create(unique_name=room_name)``
    """
    room_name = f"{settings.TWILIO_VIDEO_ROOM_PREFIX}consult-{consult_request_id}"
    return VideoRoomInfo(
        room_id=room_name,
        room_url=f"https://video.twilio.com/rooms/{room_name}",
        token=f"mock-jwt-room-{consult_request_id}",
    )


def delete_video_room(room_id: str) -> None:
    """Terminate a Twilio Video room (stub for future cleanup).

    TODO: Implement with:
        ``client.video.rooms(room_id).update(status='completed')``
    """
    # Stub – no-op until real Twilio SDK is integrated.
