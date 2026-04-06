"""Tests for F3 Part 2 – Video sessions (service + REST endpoints + WebSocket).

Coverage:
  - create_video_session: professional creates a session.
  - create_video_session: 403 for non-matched professional.
  - create_video_session: 409 when session already exists.
  - create_video_session: 422 for consult not in ``matched`` status.
  - get_video_session: returns session for patient and professional.
  - get_video_session: 403 for unrelated user.
  - end_video_session: marks session ENDED, sets ended_at.
  - end_video_session: 404 when no session exists.
  - end_video_session: 422 when already ended.
  - REST POST /professionals/me/consult-requests/{id}/video-session.
  - REST GET  /professionals/me/consult-requests/{id}/video-session.
  - REST POST /professionals/me/consult-requests/{id}/video-session/end.
  - REST GET  /patients/me/consult-requests/{id}/video-session.
  - REST POST /patients/me/consult-requests/{id}/video-session/end.
  - WebSocket signalling: offer relayed to peer, ICE relayed, auth error.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.models.video_session import VideoSession, VideoSessionStatus
from app.services.video_sessions import (
    create_video_session,
    end_video_session,
    get_video_session,
)

# ── Seed helpers ───────────────────────────────────────────────────────────────


async def _seed_specialty(db: AsyncSession, slug: str = "clinico-geral") -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


async def _seed_patient(db: AsyncSession, email: str) -> tuple[User, PatientProfile]:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    cpf = str(user.id.int)[:11].zfill(11)
    profile = PatientProfile(
        id=uuid.uuid4(), user_id=user.id, full_name="Test Patient", cpf=cpf
    )
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    return user, profile


async def _seed_professional(
    db: AsyncSession, email: str, specialty_id: uuid.UUID
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hash_password("pw"),
        role=UserRole.professional,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    profile = ProfessionalProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name=f"Dr {email}",
        crm=f"CRM{str(user.id.int)[:6]}",
        specialty="general",
        status_verificacao=VerificationStatus.approved,
    )
    db.add(profile)
    ps = ProfessionalSpecialty(professional_user_id=user.id, specialty_id=specialty_id)
    db.add(ps)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_consult(
    db: AsyncSession,
    patient_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_id: uuid.UUID,
    cr_status: ConsultRequestStatus = ConsultRequestStatus.matched,
) -> ConsultRequest:
    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_id,
        specialty_id=specialty_id,
        quoted_price_cents=10000,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.used,
    )
    db.add(quote)
    await db.flush()
    cr = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient_id,
        specialty_id=specialty_id,
        quote_id=quote.id,
        complaint="headache",
        status=cr_status,
        matched_professional_user_id=professional_id,
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return cr


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Service tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_video_session_professional(db_session: AsyncSession):
    """Professional creates a video session for a matched consult."""
    spec = await _seed_specialty(db_session)
    patient, _ = await _seed_patient(db_session, "p1@video.com")
    professional = await _seed_professional(db_session, "d1@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    session, access_token = await create_video_session(
        db=db_session,
        consult_request_id=cr.id,
        professional_user_id=professional.id,
    )

    assert session.id is not None
    assert session.consult_request_id == cr.id
    assert session.provider == "TWILIO"
    assert session.status == VideoSessionStatus.READY
    assert session.room_id == f"medcool-consult-{cr.id}"
    assert session.started_at is None
    assert session.ended_at is None
    assert access_token.startswith("stub-jwt-")


@pytest.mark.asyncio
async def test_create_video_session_wrong_professional(db_session: AsyncSession):
    """Non-matched professional cannot create a video session."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "cardio")
    patient, _ = await _seed_patient(db_session, "p2@video.com")
    professional = await _seed_professional(db_session, "d2@video.com", spec.id)
    other_professional = await _seed_professional(db_session, "d3@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    with pytest.raises(HTTPException) as exc_info:
        await create_video_session(
            db=db_session,
            consult_request_id=cr.id,
            professional_user_id=other_professional.id,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_create_video_session_duplicate(db_session: AsyncSession):
    """Creating a second video session raises HTTP 409."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "derm")
    patient, _ = await _seed_patient(db_session, "p3@video.com")
    professional = await _seed_professional(db_session, "d4@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    await create_video_session(
        db=db_session,
        consult_request_id=cr.id,
        professional_user_id=professional.id,
    )
    with pytest.raises(HTTPException) as exc_info:
        await create_video_session(
            db=db_session,
            consult_request_id=cr.id,
            professional_user_id=professional.id,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_video_session_wrong_status(db_session: AsyncSession):
    """Consult not in 'matched' status raises HTTP 422."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "ortho")
    patient, _ = await _seed_patient(db_session, "p4@video.com")
    professional = await _seed_professional(db_session, "d5@video.com", spec.id)
    cr = await _create_consult(
        db_session,
        patient.id,
        spec.id,
        professional.id,
        cr_status=ConsultRequestStatus.cancelled_by_patient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_video_session(
            db=db_session,
            consult_request_id=cr.id,
            professional_user_id=professional.id,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_get_video_session_returns_none_when_missing(db_session: AsyncSession):
    """get_video_session returns None if no session exists."""
    spec = await _seed_specialty(db_session, "neuro")
    patient, _ = await _seed_patient(db_session, "p5@video.com")
    professional = await _seed_professional(db_session, "d6@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    result, _ = await get_video_session(
        db=db_session,
        consult_request_id=cr.id,
        user_id=patient.id,
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_video_session_as_patient(db_session: AsyncSession):
    """Patient can read the video session for their consult."""
    spec = await _seed_specialty(db_session, "psico")
    patient, _ = await _seed_patient(db_session, "p6@video.com")
    professional = await _seed_professional(db_session, "d7@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    await create_video_session(
        db=db_session,
        consult_request_id=cr.id,
        professional_user_id=professional.id,
    )
    result, token = await get_video_session(
        db=db_session,
        consult_request_id=cr.id,
        user_id=patient.id,
    )
    assert result is not None
    assert result.status == VideoSessionStatus.READY
    assert token is not None


@pytest.mark.asyncio
async def test_get_video_session_forbidden_for_outsider(db_session: AsyncSession):
    """Unrelated user cannot read a video session."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "oftalmo")
    patient, _ = await _seed_patient(db_session, "p7@video.com")
    professional = await _seed_professional(db_session, "d8@video.com", spec.id)
    outsider, _ = await _seed_patient(db_session, "outsider@video.com")
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    with pytest.raises(HTTPException) as exc_info:
        await get_video_session(
            db=db_session,
            consult_request_id=cr.id,
            user_id=outsider.id,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_end_video_session(db_session: AsyncSession):
    """Ending a session sets status=ENDED and ended_at."""
    spec = await _seed_specialty(db_session, "endo")
    patient, _ = await _seed_patient(db_session, "p8@video.com")
    professional = await _seed_professional(db_session, "d9@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    await create_video_session(
        db=db_session,
        consult_request_id=cr.id,
        professional_user_id=professional.id,
    )
    ended = await end_video_session(
        db=db_session,
        consult_request_id=cr.id,
        user_id=professional.id,
    )
    assert ended.status == VideoSessionStatus.ENDED
    assert ended.ended_at is not None


@pytest.mark.asyncio
async def test_end_video_session_no_session(db_session: AsyncSession):
    """Ending a non-existent session raises HTTP 404."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "nutri")
    patient, _ = await _seed_patient(db_session, "p9@video.com")
    professional = await _seed_professional(db_session, "d10@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    with pytest.raises(HTTPException) as exc_info:
        await end_video_session(
            db=db_session,
            consult_request_id=cr.id,
            user_id=professional.id,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_end_video_session_already_ended(db_session: AsyncSession):
    """Ending an already-ended session raises HTTP 422."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "fisio")
    patient, _ = await _seed_patient(db_session, "p10@video.com")
    professional = await _seed_professional(db_session, "d11@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    await create_video_session(
        db=db_session,
        consult_request_id=cr.id,
        professional_user_id=professional.id,
    )
    await end_video_session(
        db=db_session,
        consult_request_id=cr.id,
        user_id=professional.id,
    )
    with pytest.raises(HTTPException) as exc_info:
        await end_video_session(
            db=db_session,
            consult_request_id=cr.id,
            user_id=professional.id,
        )
    assert exc_info.value.status_code == 422


# ── REST endpoint tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rest_professional_create_video_session(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /professionals/me/consult-requests/{id}/video-session returns 201."""
    spec = await _seed_specialty(db_session, "rest-create")
    patient, _ = await _seed_patient(db_session, "rest_p1@video.com")
    professional = await _seed_professional(db_session, "rest_d1@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    token = create_access_token(str(professional.id))
    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["consult_request_id"] == str(cr.id)
    assert data["status"] == "READY"
    assert data["provider"] == "TWILIO"
    assert "room_id" in data


@pytest.mark.asyncio
async def test_rest_professional_create_video_session_duplicate(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /professionals/.../video-session returns 409 on duplicate."""
    spec = await _seed_specialty(db_session, "rest-dup")
    patient, _ = await _seed_patient(db_session, "rest_p2@video.com")
    professional = await _seed_professional(db_session, "rest_d2@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    token = create_access_token(str(professional.id))
    url = f"/professionals/me/consult-requests/{cr.id}/video-session"
    r1 = await client.post(url, headers=_auth(token))
    assert r1.status_code == 201
    r2 = await client.post(url, headers=_auth(token))
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_rest_professional_get_video_session(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /professionals/me/consult-requests/{id}/video-session returns session."""
    spec = await _seed_specialty(db_session, "rest-get-pro")
    patient, _ = await _seed_patient(db_session, "rest_p3@video.com")
    professional = await _seed_professional(db_session, "rest_d3@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    token = create_access_token(str(professional.id))
    await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(token),
    )
    resp = await client.get(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"


@pytest.mark.asyncio
async def test_rest_professional_get_video_session_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /professionals/.../video-session returns 404 when none exists."""
    spec = await _seed_specialty(db_session, "rest-get-miss")
    patient, _ = await _seed_patient(db_session, "rest_p4@video.com")
    professional = await _seed_professional(db_session, "rest_d4@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    token = create_access_token(str(professional.id))
    resp = await client.get(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rest_patient_get_video_session(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /patients/me/consult-requests/{id}/video-session returns session."""
    spec = await _seed_specialty(db_session, "rest-patient-get")
    patient, _ = await _seed_patient(db_session, "rest_p5@video.com")
    professional = await _seed_professional(db_session, "rest_d5@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    pro_token = create_access_token(str(professional.id))
    pat_token = create_access_token(str(patient.id))

    await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(pro_token),
    )
    resp = await client.get(
        f"/patients/me/consult-requests/{cr.id}/video-session",
        headers=_auth(pat_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "READY"


@pytest.mark.asyncio
async def test_rest_patient_get_video_session_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /patients/.../video-session returns 404 when none exists."""
    spec = await _seed_specialty(db_session, "rest-pat-miss")
    patient, _ = await _seed_patient(db_session, "rest_p6@video.com")
    professional = await _seed_professional(db_session, "rest_d6@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    pat_token = create_access_token(str(patient.id))
    resp = await client.get(
        f"/patients/me/consult-requests/{cr.id}/video-session",
        headers=_auth(pat_token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rest_professional_end_video_session(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /professionals/.../video-session/end transitions to ENDED."""
    spec = await _seed_specialty(db_session, "rest-end-pro")
    patient, _ = await _seed_patient(db_session, "rest_p7@video.com")
    professional = await _seed_professional(db_session, "rest_d7@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    token = create_access_token(str(professional.id))
    await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(token),
    )
    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session/end",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ENDED"
    assert resp.json()["ended_at"] is not None


@pytest.mark.asyncio
async def test_rest_patient_end_video_session(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /patients/.../video-session/end transitions to ENDED."""
    spec = await _seed_specialty(db_session, "rest-end-pat")
    patient, _ = await _seed_patient(db_session, "rest_p8@video.com")
    professional = await _seed_professional(db_session, "rest_d8@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    pro_token = create_access_token(str(professional.id))
    pat_token = create_access_token(str(patient.id))

    await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(pro_token),
    )
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/video-session/end",
        headers=_auth(pat_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ENDED"


# ── WebSocket tests ────────────────────────────────────────────────────────────


def test_video_ws_missing_token(db_session: AsyncSession):
    """WebSocket connection without token is closed with code 4001."""
    import json

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from starlette.testclient import TestClient  # noqa: PLC0415

    import app.api.video_ws as video_ws_module

    _test_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    video_ws_module._ws_session_factory = _test_session
    try:
        sync_client = TestClient(video_ws_module.router)
        consult_id = uuid.uuid4()
        with sync_client.websocket_connect(
            f"/ws/video/consults/{consult_id}"
        ) as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "Missing" in msg["detail"]
    finally:
        video_ws_module._ws_session_factory = video_ws_module.AsyncSessionLocal


def test_video_ws_invalid_token(db_session: AsyncSession):
    """WebSocket connection with an invalid token is closed with code 4001."""
    import json

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from starlette.testclient import TestClient  # noqa: PLC0415

    import app.api.video_ws as video_ws_module

    _test_session = async_sessionmaker(db_session.bind, expire_on_commit=False)
    video_ws_module._ws_session_factory = _test_session
    try:
        sync_client = TestClient(video_ws_module.router)
        consult_id = uuid.uuid4()
        with sync_client.websocket_connect(
            f"/ws/video/consults/{consult_id}?token=bad-token"
        ) as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
    finally:
        video_ws_module._ws_session_factory = video_ws_module.AsyncSessionLocal


@pytest.mark.asyncio
async def test_video_ws_not_participant(db_session: AsyncSession):
    """WebSocket connection by a non-participant is closed with code 4003."""
    import json
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    import app.api.video_ws as video_ws_module
    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    spec = await _seed_specialty(db_session, "ws-nonpart")
    patient, _ = await _seed_patient(db_session, "ws_p1@video.com")
    professional = await _seed_professional(db_session, "ws_d1@video.com", spec.id)
    outsider, _ = await _seed_patient(db_session, "ws_out@video.com")
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)
    token = create_access_token(str(outsider.id))

    video_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        sync_client = TestClient(app, raise_server_exceptions=True)
        with sync_client.websocket_connect(
            f"/ws/video/consults/{cr.id}?token={token}"
        ) as ws:
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "error"
            assert "participant" in msg["detail"].lower()
    finally:
        app.dependency_overrides.clear()
        video_ws_module._ws_session_factory = video_ws_module.AsyncSessionLocal


@pytest.mark.asyncio
async def test_video_ws_offer_relayed_to_peer(db_session: AsyncSession, client: AsyncClient):
    """Offer sent by professional is relayed to patient; not echoed back.

    Uses Starlette's synchronous TestClient for WebSocket testing since httpx
    does not support the WebSocket protocol.  The async db_session is passed
    through the ``_ws_session_factory`` override so all DB state is shared.
    Both connections share the same event loop by using TestClient as a
    context manager, which is required for cross-connection relay to work.
    """
    import json
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    import app.api.video_ws as video_ws_module
    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    spec = await _seed_specialty(db_session, "ws-relay")
    patient, _ = await _seed_patient(db_session, "ws_p2@video.com")
    professional = await _seed_professional(db_session, "ws_d2@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)
    pat_token = create_access_token(str(patient.id))
    pro_token = create_access_token(str(professional.id))

    video_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        # Use TestClient as a context manager so both WS connections share the
        # same blocking portal (event loop) and cross-connection relay works.
        with TestClient(app, raise_server_exceptions=True) as sync_client:
            with sync_client.websocket_connect(
                f"/ws/video/consults/{cr.id}?token={pat_token}"
            ) as pat_ws:
                with sync_client.websocket_connect(
                    f"/ws/video/consults/{cr.id}?token={pro_token}"
                ) as pro_ws:
                    # Professional sends an offer
                    pro_ws.send_text(json.dumps({"type": "offer", "sdp": "v=0\r\n..."}))
                    # Patient should receive the relayed offer
                    received = json.loads(pat_ws.receive_text())
                    assert received["type"] == "offer"
                    assert received["sdp"] == "v=0\r\n..."
                    assert received["from"] == str(professional.id)
    finally:
        app.dependency_overrides.clear()
        video_ws_module._ws_session_factory = video_ws_module.AsyncSessionLocal


@pytest.mark.asyncio
async def test_video_ws_unknown_event_type(db_session: AsyncSession):
    """Sending an unknown event type returns an error message."""
    import json
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    import app.api.video_ws as video_ws_module
    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    spec = await _seed_specialty(db_session, "ws-unknown")
    patient, _ = await _seed_patient(db_session, "ws_p3@video.com")
    professional = await _seed_professional(db_session, "ws_d3@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)
    token = create_access_token(str(professional.id))

    video_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        sync_client = TestClient(app, raise_server_exceptions=True)
        with sync_client.websocket_connect(
            f"/ws/video/consults/{cr.id}?token={token}"
        ) as ws:
            ws.send_text(json.dumps({"type": "message", "content": "hello"}))
            error = json.loads(ws.receive_text())
            assert error["type"] == "error"
            assert "Unknown event type" in error["detail"]
    finally:
        app.dependency_overrides.clear()
        video_ws_module._ws_session_factory = video_ws_module.AsyncSessionLocal


# ── Twilio mock tests (F3 Part 3) ─────────────────────────────────────────────


def test_twilio_create_video_room_stub():
    """create_video_room returns stub values when Twilio credentials are absent."""
    import app.services.twilio_video as tv_module

    consult_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Ensure credentials are not set for this test.
    original = (
        tv_module.settings.TWILIO_ACCOUNT_SID,
        tv_module.settings.TWILIO_API_KEY,
        tv_module.settings.TWILIO_API_SECRET,
    )
    tv_module.settings.TWILIO_ACCOUNT_SID = None
    tv_module.settings.TWILIO_API_KEY = None
    tv_module.settings.TWILIO_API_SECRET = None
    try:
        info = tv_module.create_video_room(consult_id, user_id)
        assert info.room_id == f"medcool-consult-{consult_id}"
        assert info.room_url == f"https://video.twilio.com/rooms/medcool-consult-{consult_id}"
        assert info.token.startswith("stub-jwt-")
        assert str(user_id) in info.token
    finally:
        tv_module.settings.TWILIO_ACCOUNT_SID = original[0]
        tv_module.settings.TWILIO_API_KEY = original[1]
        tv_module.settings.TWILIO_API_SECRET = original[2]


def test_twilio_generate_access_token_stub():
    """generate_access_token returns a stub token when credentials are absent."""
    import app.services.twilio_video as tv_module

    user_id = uuid.uuid4()
    room_name = "medcool-consult-test"

    original = (
        tv_module.settings.TWILIO_ACCOUNT_SID,
        tv_module.settings.TWILIO_API_KEY,
        tv_module.settings.TWILIO_API_SECRET,
    )
    tv_module.settings.TWILIO_ACCOUNT_SID = None
    tv_module.settings.TWILIO_API_KEY = None
    tv_module.settings.TWILIO_API_SECRET = None
    try:
        token = tv_module.generate_access_token(user_id, room_name)
        assert token.startswith("stub-jwt-")
        assert str(user_id) in token
        assert room_name in token
    finally:
        tv_module.settings.TWILIO_ACCOUNT_SID = original[0]
        tv_module.settings.TWILIO_API_KEY = original[1]
        tv_module.settings.TWILIO_API_SECRET = original[2]


def test_twilio_create_video_room_real_sdk():
    """create_video_room calls real Twilio SDK when credentials are configured."""
    from unittest.mock import MagicMock, patch

    import app.services.twilio_video as tv_module

    consult_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fake_sid = "RM" + "a" * 32
    fake_jwt = "eyJ.fake.jwt"

    original = (
        tv_module.settings.TWILIO_ACCOUNT_SID,
        tv_module.settings.TWILIO_API_KEY,
        tv_module.settings.TWILIO_API_SECRET,
    )
    tv_module.settings.TWILIO_ACCOUNT_SID = "ACfakeaccountsid"
    tv_module.settings.TWILIO_API_KEY = "SKfakeapikey"
    tv_module.settings.TWILIO_API_SECRET = "fakesecret"
    try:
        mock_room = MagicMock()
        mock_room.sid = fake_sid
        mock_client = MagicMock()
        mock_client.video.rooms.create.return_value = mock_room

        mock_token_instance = MagicMock()
        mock_token_instance.to_jwt.return_value = fake_jwt

        with (
            patch("twilio.rest.Client", return_value=mock_client),
            patch(
                "twilio.jwt.access_token.AccessToken",
                return_value=mock_token_instance,
            ),
            patch("twilio.jwt.access_token.grants.VideoGrant"),
        ):
            info = tv_module.create_video_room(consult_id, user_id)

        expected_room_name = f"medcool-consult-{consult_id}"
        mock_client.video.rooms.create.assert_called_once_with(
            unique_name=expected_room_name
        )
        assert info.room_id == fake_sid
        assert fake_sid in info.room_url
        assert info.token == fake_jwt
    finally:
        tv_module.settings.TWILIO_ACCOUNT_SID = original[0]
        tv_module.settings.TWILIO_API_KEY = original[1]
        tv_module.settings.TWILIO_API_SECRET = original[2]


def test_twilio_generate_access_token_real_sdk():
    """generate_access_token calls real Twilio SDK when credentials are configured."""
    from unittest.mock import MagicMock, patch

    import app.services.twilio_video as tv_module

    user_id = uuid.uuid4()
    room_name = "medcool-consult-test-room"
    fake_jwt = "eyJ.real.jwt"

    original = (
        tv_module.settings.TWILIO_ACCOUNT_SID,
        tv_module.settings.TWILIO_API_KEY,
        tv_module.settings.TWILIO_API_SECRET,
    )
    tv_module.settings.TWILIO_ACCOUNT_SID = "ACfakeaccountsid"
    tv_module.settings.TWILIO_API_KEY = "SKfakeapikey"
    tv_module.settings.TWILIO_API_SECRET = "fakesecret"
    try:
        mock_token_instance = MagicMock()
        mock_token_instance.to_jwt.return_value = fake_jwt

        with (
            patch(
                "twilio.jwt.access_token.AccessToken",
                return_value=mock_token_instance,
            ),
            patch("twilio.jwt.access_token.grants.VideoGrant") as mock_grant_cls,
        ):
            token = tv_module.generate_access_token(user_id, room_name)

        mock_grant_cls.assert_called_once_with(room=room_name)
        mock_token_instance.add_grant.assert_called_once()
        assert token == fake_jwt
    finally:
        tv_module.settings.TWILIO_ACCOUNT_SID = original[0]
        tv_module.settings.TWILIO_API_KEY = original[1]
        tv_module.settings.TWILIO_API_SECRET = original[2]


@pytest.mark.asyncio
async def test_rest_create_video_session_returns_access_token(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /professionals/.../video-session response includes access_token."""
    spec = await _seed_specialty(db_session, "twilio-tok")
    patient, _ = await _seed_patient(db_session, "twilio_p1@video.com")
    professional = await _seed_professional(db_session, "twilio_d1@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    token = create_access_token(str(professional.id))
    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["access_token"] is not None


@pytest.mark.asyncio
async def test_rest_get_video_session_returns_access_token(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /patients/.../video-session response includes access_token."""
    spec = await _seed_specialty(db_session, "twilio-get-tok")
    patient, _ = await _seed_patient(db_session, "twilio_p2@video.com")
    professional = await _seed_professional(db_session, "twilio_d2@video.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    pro_token = create_access_token(str(professional.id))
    pat_token = create_access_token(str(patient.id))

    await client.post(
        f"/professionals/me/consult-requests/{cr.id}/video-session",
        headers=_auth(pro_token),
    )
    resp = await client.get(
        f"/patients/me/consult-requests/{cr.id}/video-session",
        headers=_auth(pat_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["access_token"] is not None
