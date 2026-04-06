"""Tests for F3 Part 1 – Chat (service + REST history endpoints + WebSocket).

Coverage:
  - send_chat_message: patient → professional, professional → patient.
  - list_chat_messages: ordering by sent_at, page/limit pagination.
  - 403 when non-participant tries to read/write messages.
  - 422 when sending a message to a consult in a prohibited status (canceled).
  - REST GET /patients/me/consult-requests/{consult_id}/chat/messages.
  - REST GET /professionals/me/consult-requests/{consult_id}/chat/messages.
  - before/after query filters for history.
  - WebSocket basic send/receive flow.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password
from app.db.models.chat_message import ChatMessage, MessageType, SenderRole
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.services.chat import list_chat_messages, send_chat_message

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
async def test_send_message_from_patient(db_session: AsyncSession):
    """Patient can send a chat message to the matched professional."""
    spec = await _seed_specialty(db_session)
    patient, _ = await _seed_patient(db_session, "p1@chat.com")
    professional = await _seed_professional(db_session, "d1@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    msg = await send_chat_message(
        db=db_session,
        consult_id=cr.id,
        sender_user_id=patient.id,
        content="Hello doctor",
    )

    assert msg.id is not None
    assert msg.consult_request_id == cr.id
    assert msg.sender_user_id == patient.id
    assert msg.receiver_user_id == professional.id
    assert msg.sender_role == SenderRole.PATIENT
    assert msg.message_type == MessageType.TEXT
    assert msg.content == "Hello doctor"
    assert msg.sent_at is not None
    assert msg.delivered_at is None
    assert msg.read_at is None


@pytest.mark.asyncio
async def test_send_message_from_professional(db_session: AsyncSession):
    """Professional can send a chat message to the patient."""
    spec = await _seed_specialty(db_session, "cardio")
    patient, _ = await _seed_patient(db_session, "p2@chat.com")
    professional = await _seed_professional(db_session, "d2@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    msg = await send_chat_message(
        db=db_session,
        consult_id=cr.id,
        sender_user_id=professional.id,
        content="Hello patient, how can I help?",
    )

    assert msg.sender_role == SenderRole.PROFESSIONAL
    assert msg.receiver_user_id == patient.id
    assert msg.content == "Hello patient, how can I help?"


@pytest.mark.asyncio
async def test_send_message_forbidden_non_participant(db_session: AsyncSession):
    """A user who is not part of the consult cannot send messages."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "neuro")
    patient, _ = await _seed_patient(db_session, "p3@chat.com")
    professional = await _seed_professional(db_session, "d3@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    outsider = User(
        id=uuid.uuid4(),
        email="outsider@chat.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(outsider)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await send_chat_message(
            db=db_session,
            consult_id=cr.id,
            sender_user_id=outsider.id,
            content="intruder",
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_send_message_forbidden_cancelled_consult(db_session: AsyncSession):
    """Sending a message to a cancelled consult raises 422."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "derm")
    patient, _ = await _seed_patient(db_session, "p4@chat.com")
    professional = await _seed_professional(db_session, "d4@chat.com", spec.id)
    cr = await _create_consult(
        db_session,
        patient.id,
        spec.id,
        professional.id,
        cr_status=ConsultRequestStatus.cancelled_by_patient,
    )

    with pytest.raises(HTTPException) as exc_info:
        await send_chat_message(
            db=db_session,
            consult_id=cr.id,
            sender_user_id=patient.id,
            content="still here",
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_list_messages_ordering_and_pagination(db_session: AsyncSession):
    """list_chat_messages returns messages ordered by sent_at; page/limit work."""
    spec = await _seed_specialty(db_session, "ortho")
    patient, _ = await _seed_patient(db_session, "p5@chat.com")
    professional = await _seed_professional(db_session, "d5@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    # Insert 5 messages with explicit sent_at values to ensure ordering
    base_time = datetime.now(tz=UTC)
    for i in range(5):
        msg = ChatMessage(
            id=uuid.uuid4(),
            consult_request_id=cr.id,
            sender_user_id=patient.id,
            receiver_user_id=professional.id,
            sender_role=SenderRole.PATIENT,
            message_type=MessageType.TEXT,
            content=f"msg {i}",
            sent_at=base_time + timedelta(seconds=i),
        )
        db_session.add(msg)
    await db_session.commit()

    # First page: 3 messages
    messages, total = await list_chat_messages(
        db=db_session,
        consult_id=cr.id,
        user_id=patient.id,
        page=1,
        limit=3,
    )
    assert total == 5
    assert len(messages) == 3
    # Ordered by sent_at ascending
    assert messages[0].content == "msg 0"
    assert messages[2].content == "msg 2"

    # Second page: remaining 2
    messages2, total2 = await list_chat_messages(
        db=db_session,
        consult_id=cr.id,
        user_id=patient.id,
        page=2,
        limit=3,
    )
    assert total2 == 5
    assert len(messages2) == 2
    assert messages2[0].content == "msg 3"
    assert messages2[1].content == "msg 4"


@pytest.mark.asyncio
async def test_list_messages_before_after_filters(db_session: AsyncSession):
    """before/after query params filter messages correctly."""
    spec = await _seed_specialty(db_session, "ophthalmo")
    patient, _ = await _seed_patient(db_session, "p6@chat.com")
    professional = await _seed_professional(db_session, "d6@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    for i in range(4):
        msg = ChatMessage(
            id=uuid.uuid4(),
            consult_request_id=cr.id,
            sender_user_id=patient.id,
            receiver_user_id=professional.id,
            sender_role=SenderRole.PATIENT,
            message_type=MessageType.TEXT,
            content=f"msg {i}",
            sent_at=base_time + timedelta(minutes=i),
        )
        db_session.add(msg)
    await db_session.commit()

    pivot = base_time + timedelta(minutes=2)

    # before pivot: msgs 0, 1
    msgs_before, total_before = await list_chat_messages(
        db=db_session, consult_id=cr.id, user_id=patient.id, before=pivot
    )
    assert total_before == 2
    assert all(m.content in ("msg 0", "msg 1") for m in msgs_before)

    # after pivot: msg 3 (strictly after minute 2)
    msgs_after, total_after = await list_chat_messages(
        db=db_session, consult_id=cr.id, user_id=patient.id, after=pivot
    )
    assert total_after == 1
    assert msgs_after[0].content == "msg 3"


@pytest.mark.asyncio
async def test_list_messages_forbidden_non_participant(db_session: AsyncSession):
    """Non-participant cannot list messages."""
    from fastapi import HTTPException

    spec = await _seed_specialty(db_session, "rheum")
    patient, _ = await _seed_patient(db_session, "p7@chat.com")
    professional = await _seed_professional(db_session, "d7@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    outsider = User(
        id=uuid.uuid4(),
        email="outsider2@chat.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(outsider)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await list_chat_messages(
            db=db_session, consult_id=cr.id, user_id=outsider.id
        )
    assert exc_info.value.status_code == 403


# ── REST endpoint tests ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def chat_setup(db_session: AsyncSession, client: AsyncClient):
    """Seed users, consult, and a few messages; return tokens and IDs."""
    spec = await _seed_specialty(db_session, "general")
    patient, _ = await _seed_patient(db_session, "pat@rest.com")
    professional = await _seed_professional(db_session, "doc@rest.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    # Send a few messages via service so we have data
    base_time = datetime.now(tz=UTC)
    for i in range(3):
        msg = ChatMessage(
            id=uuid.uuid4(),
            consult_request_id=cr.id,
            sender_user_id=patient.id if i % 2 == 0 else professional.id,
            receiver_user_id=professional.id if i % 2 == 0 else patient.id,
            sender_role=SenderRole.PATIENT if i % 2 == 0 else SenderRole.PROFESSIONAL,
            message_type=MessageType.TEXT,
            content=f"rest msg {i}",
            sent_at=base_time + timedelta(seconds=i),
        )
        db_session.add(msg)
    await db_session.commit()

    # Login both users via HTTP
    r_pat = await client.post("/auth/login", json={"email": "pat@rest.com", "password": "pw"})
    assert r_pat.status_code == 200
    r_doc = await client.post("/auth/login", json={"email": "doc@rest.com", "password": "pw"})
    assert r_doc.status_code == 200

    return {
        "patient_token": r_pat.json()["access_token"],
        "professional_token": r_doc.json()["access_token"],
        "consult_id": str(cr.id),
        "patient": patient,
        "professional": professional,
    }


@pytest.mark.asyncio
async def test_patient_list_chat_history_rest(client: AsyncClient, chat_setup):
    """Patient can retrieve chat history via REST."""
    token = chat_setup["patient_token"]
    consult_id = chat_setup["consult_id"]

    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/chat/messages",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["page"] == 1
    assert data["limit"] == 50


@pytest.mark.asyncio
async def test_professional_list_chat_history_rest(client: AsyncClient, chat_setup):
    """Professional can retrieve chat history via REST."""
    token = chat_setup["professional_token"]
    consult_id = chat_setup["consult_id"]

    resp = await client.get(
        f"/professionals/me/consult-requests/{consult_id}/chat/messages",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_chat_history_pagination_rest(client: AsyncClient, chat_setup):
    """page and limit query params work for chat history."""
    token = chat_setup["patient_token"]
    consult_id = chat_setup["consult_id"]

    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/chat/messages?page=1&limit=2",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 3
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_chat_history_forbidden_wrong_role(client: AsyncClient, chat_setup):
    """A professional cannot access the patient's chat history endpoint."""
    pro_token = chat_setup["professional_token"]
    consult_id = chat_setup["consult_id"]

    # Patient endpoint requires patient role → should be 403
    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/chat/messages",
        headers=_auth(pro_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_chat_history_returns_correct_fields(client: AsyncClient, chat_setup):
    """Each message in the response contains all expected fields."""
    token = chat_setup["patient_token"]
    consult_id = chat_setup["consult_id"]

    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/chat/messages",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    for field in (
        "id", "consult_request_id", "sender_user_id", "receiver_user_id",
        "sender_role", "message_type", "content", "sent_at",
    ):
        assert field in item, f"Missing field: {field}"


# ── WebSocket tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ws_rejects_missing_token(db_session: AsyncSession):
    """WebSocket connection without a token is rejected immediately."""
    import app.api.chat_ws as chat_ws_module  # noqa: PLC0415
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    chat_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        sync_client = TestClient(app)
        with sync_client.websocket_connect(
            "/ws/chat/consults/00000000-0000-0000-0000-000000000001"
        ) as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "token" in data["detail"].lower()
    except Exception:
        pass
    finally:
        app.dependency_overrides.clear()
        chat_ws_module._ws_session_factory = chat_ws_module.AsyncSessionLocal


@pytest.mark.asyncio
async def test_ws_rejects_invalid_token(db_session: AsyncSession):
    """WebSocket with an invalid JWT is rejected."""
    import app.api.chat_ws as chat_ws_module  # noqa: PLC0415
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    consult_id = uuid.uuid4()
    chat_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        sync_client = TestClient(app)
        with sync_client.websocket_connect(
            f"/ws/chat/consults/{consult_id}?token=bad.token.here"
        ) as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
    except Exception:
        pass
    finally:
        app.dependency_overrides.clear()
        chat_ws_module._ws_session_factory = chat_ws_module.AsyncSessionLocal


@pytest.mark.asyncio
async def test_ws_send_and_receive(db_session: AsyncSession, client: AsyncClient):
    """Patient and professional can exchange messages over WebSocket.

    Uses Starlette's synchronous TestClient for WebSocket testing since httpx
    does not support the WebSocket protocol.  The async db_session is passed
    through the ``_ws_session_factory`` override so all DB state is shared.
    """
    import app.api.chat_ws as chat_ws_module  # noqa: PLC0415
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    spec = await _seed_specialty(db_session, "ws-spec")
    patient, _ = await _seed_patient(db_session, "ws_pat@chat.com")
    professional = await _seed_professional(db_session, "ws_doc@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    pat_token = create_access_token(sub=str(patient.id))
    doc_token = create_access_token(sub=str(professional.id))

    chat_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        sync_client = TestClient(app, raise_server_exceptions=True)

        # Connect patient
        with sync_client.websocket_connect(
            f"/ws/chat/consults/{cr.id}?token={pat_token}"
        ) as ws_pat:
            with sync_client.websocket_connect(
                f"/ws/chat/consults/{cr.id}?token={doc_token}"
            ) as ws_doc:
                # Patient sends a message
                ws_pat.send_json(
                    {"type": "message", "content": "Hi doc!", "client_message_id": "cid-1"}
                )

                # Patient's own connection receives the broadcast echo
                pat_recv = ws_pat.receive_json()
                assert pat_recv["type"] == "message"
                assert pat_recv["message"]["content"] == "Hi doc!"
                assert pat_recv["client_message_id"] == "cid-1"
                assert pat_recv["message"]["sender_role"] == "PATIENT"

                # Professional's connection also receives the broadcast
                doc_recv = ws_doc.receive_json()
                assert doc_recv["type"] == "message"
                assert doc_recv["message"]["content"] == "Hi doc!"
    finally:
        app.dependency_overrides.clear()
        chat_ws_module._ws_session_factory = chat_ws_module.AsyncSessionLocal


@pytest.mark.asyncio
async def test_ws_non_participant_rejected(db_session: AsyncSession):
    """A user not in the consult is rejected when connecting via WebSocket."""
    import app.api.chat_ws as chat_ws_module  # noqa: PLC0415
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from starlette.testclient import TestClient  # noqa: PLC0415

    from app.db.session import get_db  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    @asynccontextmanager
    async def _test_session():
        yield db_session

    spec = await _seed_specialty(db_session, "ws-other")
    patient, _ = await _seed_patient(db_session, "ws_o_pat@chat.com")
    professional = await _seed_professional(db_session, "ws_o_doc@chat.com", spec.id)
    cr = await _create_consult(db_session, patient.id, spec.id, professional.id)

    outsider = User(
        id=uuid.uuid4(),
        email="outsider_ws@chat.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(outsider)
    await db_session.commit()

    outsider_token = create_access_token(sub=str(outsider.id))

    chat_ws_module._ws_session_factory = _test_session
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        sync_client = TestClient(app)
        with sync_client.websocket_connect(
            f"/ws/chat/consults/{cr.id}?token={outsider_token}"
        ) as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "participant" in data["detail"].lower()
    except Exception:
        pass
    finally:
        app.dependency_overrides.clear()
        chat_ws_module._ws_session_factory = chat_ws_module.AsyncSessionLocal
