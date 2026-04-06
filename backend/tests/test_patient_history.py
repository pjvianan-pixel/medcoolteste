"""Tests for F6 Part 1 – Patient Consult History."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import DocumentStatus, DocumentType, MedicalDocument, SignatureType
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole

# ── Seed helpers ───────────────────────────────────────────────────────────────


async def _seed_specialty(db: AsyncSession, slug: str = "clinico-geral") -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


async def _seed_patient(db: AsyncSession, email: str = "patient@history.com") -> tuple[User, PatientProfile]:
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
        id=uuid.uuid4(), user_id=user.id, full_name="Patient History", cpf=cpf
    )
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    return user, profile


async def _seed_professional(db: AsyncSession, email: str, specialty_id: uuid.UUID) -> User:
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
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_consult(
    db: AsyncSession,
    patient_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_id: uuid.UUID | None = None,
    cr_status: ConsultRequestStatus = ConsultRequestStatus.matched,
    created_at: datetime | None = None,
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
        complaint="test complaint",
        status=cr_status,
        matched_professional_user_id=professional_id,
        scheduled_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    if created_at is not None:
        cr.created_at = created_at
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return cr


async def _seed_payment(
    db: AsyncSession,
    consult_id: uuid.UUID,
    patient_id: uuid.UUID,
    professional_id: uuid.UUID | None = None,
    pay_status: PaymentStatus = PaymentStatus.paid,
    amount_cents: int = 10000,
) -> Payment:
    payment = Payment(
        id=uuid.uuid4(),
        consult_request_id=consult_id,
        patient_user_id=patient_id,
        professional_user_id=professional_id,
        amount_cents=amount_cents,
        currency="BRL",
        platform_fee_cents=1000,
        professional_amount_cents=9000,
        provider="pagarme",
        status=pay_status,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


async def _seed_document(
    db: AsyncSession,
    consult_id: uuid.UUID,
    professional_id: uuid.UUID,
    patient_id: uuid.UUID,
    doc_type: DocumentType = DocumentType.PRESCRIPTION,
    doc_status: DocumentStatus = DocumentStatus.DRAFT,
    file_url: str | None = None,
) -> MedicalDocument:
    doc = MedicalDocument(
        id=uuid.uuid4(),
        consult_request_id=consult_id,
        professional_user_id=professional_id,
        patient_user_id=patient_id,
        document_type=doc_type,
        content_json=[{"drug_name": "Aspirina", "dosage": "500mg", "instructions": "1x/day"}]
        if doc_type == DocumentType.PRESCRIPTION
        else [{"exam_name": "Hemograma", "type": "LAB"}],
        status=doc_status,
        signature_type=SignatureType.SIMPLE if doc_status == DocumentStatus.SIGNED else SignatureType.NONE,
        signed_at=datetime.now(tz=UTC) if doc_status == DocumentStatus.SIGNED else None,
        file_url=file_url,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def _login(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_empty(client: AsyncClient, db_session: AsyncSession):
    """Patient with no consults gets an empty history."""
    patient, _ = await _seed_patient(db_session, "empty@history.com")
    token = await _login(client, "empty@history.com")

    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1
    assert data["limit"] == 20


@pytest.mark.asyncio
async def test_history_multiple_consults(client: AsyncClient, db_session: AsyncSession):
    """History returns all consults for the patient in various states."""
    spec = await _seed_specialty(db_session, "cardio")
    patient, _ = await _seed_patient(db_session, "multi@history.com")
    professional = await _seed_professional(db_session, "doc.multi@history.com", spec.id)

    cr1 = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.matched)
    cr2 = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.cancelled_by_patient)
    cr3 = await _seed_consult(db_session, patient.id, spec.id, None, ConsultRequestStatus.no_show_patient)

    token = await _login(client, "multi@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3

    consult_ids = {item["consult_id"] for item in data["items"]}
    assert str(cr1.id) in consult_ids
    assert str(cr2.id) in consult_ids
    assert str(cr3.id) in consult_ids


@pytest.mark.asyncio
async def test_history_with_payment(client: AsyncClient, db_session: AsyncSession):
    """History includes payment summary when payment exists."""
    spec = await _seed_specialty(db_session, "cardio2")
    patient, _ = await _seed_patient(db_session, "pay@history.com")
    professional = await _seed_professional(db_session, "doc.pay@history.com", spec.id)

    cr = await _seed_consult(db_session, patient.id, spec.id, professional.id)
    await _seed_payment(db_session, cr.id, patient.id, professional.id, PaymentStatus.paid, 10000)

    token = await _login(client, "pay@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    payment = data["items"][0]["payment"]
    assert payment is not None
    assert payment["status"] == "paid"
    assert payment["amount_total_cents"] == 10000
    assert payment["refunded_amount_cents"] == 0


@pytest.mark.asyncio
async def test_history_refunded_payment(client: AsyncClient, db_session: AsyncSession):
    """History shows refunded_amount_cents populated when payment is refunded."""
    spec = await _seed_specialty(db_session, "dermo")
    patient, _ = await _seed_patient(db_session, "refund@history.com")
    professional = await _seed_professional(db_session, "doc.refund@history.com", spec.id)

    cr = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.cancelled_by_patient)
    await _seed_payment(db_session, cr.id, patient.id, professional.id, PaymentStatus.refunded, 8000)

    token = await _login(client, "refund@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    payment = resp.json()["items"][0]["payment"]
    assert payment["status"] == "refunded"
    assert payment["refunded_amount_cents"] == 8000


@pytest.mark.asyncio
async def test_history_no_payment(client: AsyncClient, db_session: AsyncSession):
    """History items without a payment have payment=null."""
    spec = await _seed_specialty(db_session, "nopay")
    patient, _ = await _seed_patient(db_session, "nopay@history.com")

    await _seed_consult(db_session, patient.id, spec.id, None, ConsultRequestStatus.queued)

    token = await _login(client, "nopay@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"][0]["payment"] is None


@pytest.mark.asyncio
async def test_history_signed_document_has_file_url(client: AsyncClient, db_session: AsyncSession):
    """Signed documents include file_url; draft documents have file_url=null."""
    spec = await _seed_specialty(db_session, "signed-doc")
    patient, _ = await _seed_patient(db_session, "signed@history.com")
    professional = await _seed_professional(db_session, "doc.signed@history.com", spec.id)

    cr = await _seed_consult(db_session, patient.id, spec.id, professional.id)
    # Signed document with file_url
    await _seed_document(
        db_session, cr.id, professional.id, patient.id,
        doc_type=DocumentType.PRESCRIPTION,
        doc_status=DocumentStatus.SIGNED,
        file_url="/static/documents/test.pdf",
    )
    # Draft document – file_url should be null in response
    await _seed_document(
        db_session, cr.id, professional.id, patient.id,
        doc_type=DocumentType.EXAM_REQUEST,
        doc_status=DocumentStatus.DRAFT,
        file_url=None,
    )

    token = await _login(client, "signed@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    docs = resp.json()["items"][0]["documents"]
    assert len(docs) == 2

    signed = next(d for d in docs if d["status"] == "SIGNED")
    draft = next(d for d in docs if d["status"] == "DRAFT")

    assert signed["file_url"] == "/static/documents/test.pdf"
    assert draft["file_url"] is None


@pytest.mark.asyncio
async def test_history_filter_by_status(client: AsyncClient, db_session: AsyncSession):
    """consult_status filter returns only matching consults."""
    spec = await _seed_specialty(db_session, "status-filter")
    patient, _ = await _seed_patient(db_session, "statusflt@history.com")
    professional = await _seed_professional(db_session, "doc.statusflt@history.com", spec.id)

    await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.matched)
    cr_cancelled = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.cancelled_by_patient)

    token = await _login(client, "statusflt@history.com")
    resp = await client.get(
        "/patients/me/history/consults?consult_status=cancelled_by_patient",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_cancelled.id)


@pytest.mark.asyncio
async def test_history_filter_invalid_status(client: AsyncClient, db_session: AsyncSession):
    """Invalid consult_status returns 422."""
    patient, _ = await _seed_patient(db_session, "badflt@history.com")
    token = await _login(client, "badflt@history.com")

    resp = await client.get(
        "/patients/me/history/consults?consult_status=nonexistent",
        headers=_auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_history_filter_from_date(client: AsyncClient, db_session: AsyncSession):
    """from_date filter excludes consults created before that date."""
    spec = await _seed_specialty(db_session, "date-filter")
    patient, _ = await _seed_patient(db_session, "dateflt@history.com")
    professional = await _seed_professional(db_session, "doc.dateflt@history.com", spec.id)

    old_date = datetime.now(tz=UTC) - timedelta(days=10)
    recent_date = datetime.now(tz=UTC) - timedelta(days=1)

    cr_old = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.matched, created_at=old_date)
    cr_recent = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.matched, created_at=recent_date)

    cutoff = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat()
    token = await _login(client, "dateflt@history.com")
    resp = await client.get(
        "/patients/me/history/consults",
        params={"from_date": cutoff},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["consult_id"] for item in data["items"]}
    assert str(cr_recent.id) in ids
    assert str(cr_old.id) not in ids


@pytest.mark.asyncio
async def test_history_filter_to_date(client: AsyncClient, db_session: AsyncSession):
    """to_date filter excludes consults created after that date."""
    spec = await _seed_specialty(db_session, "to-date-filter")
    patient, _ = await _seed_patient(db_session, "todateflt@history.com")
    professional = await _seed_professional(db_session, "doc.todateflt@history.com", spec.id)

    old_date = datetime.now(tz=UTC) - timedelta(days=10)
    recent_date = datetime.now(tz=UTC) - timedelta(days=1)

    cr_old = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.matched, created_at=old_date)
    cr_recent = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.matched, created_at=recent_date)

    cutoff = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat()
    token = await _login(client, "todateflt@history.com")
    resp = await client.get(
        "/patients/me/history/consults",
        params={"to_date": cutoff},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["consult_id"] for item in data["items"]}
    assert str(cr_old.id) in ids
    assert str(cr_recent.id) not in ids


@pytest.mark.asyncio
async def test_history_filter_has_documents_true(client: AsyncClient, db_session: AsyncSession):
    """has_documents=true returns only consults with at least one document."""
    spec = await _seed_specialty(db_session, "has-doc")
    patient, _ = await _seed_patient(db_session, "hasdoc@history.com")
    professional = await _seed_professional(db_session, "doc.hasdoc@history.com", spec.id)

    cr_with_doc = await _seed_consult(db_session, patient.id, spec.id, professional.id)
    cr_no_doc = await _seed_consult(db_session, patient.id, spec.id, professional.id)

    await _seed_document(db_session, cr_with_doc.id, professional.id, patient.id)

    token = await _login(client, "hasdoc@history.com")
    resp = await client.get(
        "/patients/me/history/consults?has_documents=true",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_with_doc.id)


@pytest.mark.asyncio
async def test_history_filter_has_documents_false(client: AsyncClient, db_session: AsyncSession):
    """has_documents=false returns only consults with no documents."""
    spec = await _seed_specialty(db_session, "no-doc")
    patient, _ = await _seed_patient(db_session, "nodoc@history.com")
    professional = await _seed_professional(db_session, "doc.nodoc@history.com", spec.id)

    cr_with_doc = await _seed_consult(db_session, patient.id, spec.id, professional.id)
    cr_no_doc = await _seed_consult(db_session, patient.id, spec.id, professional.id)

    await _seed_document(db_session, cr_with_doc.id, professional.id, patient.id)

    token = await _login(client, "nodoc@history.com")
    resp = await client.get(
        "/patients/me/history/consults?has_documents=false",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_no_doc.id)


@pytest.mark.asyncio
async def test_history_pagination(client: AsyncClient, db_session: AsyncSession):
    """Pagination returns correct page and limit."""
    spec = await _seed_specialty(db_session, "pagination")
    patient, _ = await _seed_patient(db_session, "page@history.com")
    professional = await _seed_professional(db_session, "doc.page@history.com", spec.id)

    consults = []
    for _ in range(5):
        cr = await _seed_consult(db_session, patient.id, spec.id, professional.id)
        consults.append(cr)

    token = await _login(client, "page@history.com")

    # Page 1, limit 2
    resp = await client.get(
        "/patients/me/history/consults?page=1&limit=2",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["limit"] == 2

    # Page 3, limit 2 → only 1 item remaining
    resp2 = await client.get(
        "/patients/me/history/consults?page=3&limit=2",
        headers=_auth(token),
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 5
    assert len(data2["items"]) == 1

    # Page 4, limit 2 → empty
    resp3 = await client.get(
        "/patients/me/history/consults?page=4&limit=2",
        headers=_auth(token),
    )
    assert resp3.status_code == 200
    assert resp3.json()["items"] == []


@pytest.mark.asyncio
async def test_history_isolation_between_patients(client: AsyncClient, db_session: AsyncSession):
    """A patient cannot see another patient's consults."""
    spec = await _seed_specialty(db_session, "isolation")
    patient_a, _ = await _seed_patient(db_session, "pa@history.com")
    patient_b, _ = await _seed_patient(db_session, "pb@history.com")
    professional = await _seed_professional(db_session, "doc.isolation@history.com", spec.id)

    await _seed_consult(db_session, patient_a.id, spec.id, professional.id)
    await _seed_consult(db_session, patient_b.id, spec.id, professional.id)

    token_b = await _login(client, "pb@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token_b))
    assert resp.status_code == 200
    data = resp.json()
    # Patient B sees only their own consult
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_history_detail_found(client: AsyncClient, db_session: AsyncSession):
    """GET /patients/me/history/consults/{id} returns the correct item."""
    spec = await _seed_specialty(db_session, "detail-ok")
    patient, _ = await _seed_patient(db_session, "detail@history.com")
    professional = await _seed_professional(db_session, "doc.detail@history.com", spec.id)

    cr = await _seed_consult(db_session, patient.id, spec.id, professional.id)
    await _seed_payment(db_session, cr.id, patient.id, professional.id, PaymentStatus.paid)

    token = await _login(client, "detail@history.com")
    resp = await client.get(f"/patients/me/history/consults/{cr.id}", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["consult_id"] == str(cr.id)
    assert data["status"] == "matched"
    assert data["payment"] is not None
    assert data["payment"]["status"] == "paid"


@pytest.mark.asyncio
async def test_history_detail_not_found(client: AsyncClient, db_session: AsyncSession):
    """GET /patients/me/history/consults/{id} returns 404 for unknown id."""
    patient, _ = await _seed_patient(db_session, "detailnf@history.com")
    token = await _login(client, "detailnf@history.com")

    resp = await client.get(
        f"/patients/me/history/consults/{uuid.uuid4()}",
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_detail_forbidden_other_patient(client: AsyncClient, db_session: AsyncSession):
    """GET /patients/me/history/consults/{id} returns 404 for another patient's consult."""
    spec = await _seed_specialty(db_session, "forbidden")
    patient_a, _ = await _seed_patient(db_session, "forbidden_a@history.com")
    patient_b, _ = await _seed_patient(db_session, "forbidden_b@history.com")
    professional = await _seed_professional(db_session, "doc.forbidden@history.com", spec.id)

    cr_a = await _seed_consult(db_session, patient_a.id, spec.id, professional.id)

    token_b = await _login(client, "forbidden_b@history.com")
    resp = await client.get(
        f"/patients/me/history/consults/{cr_a.id}",
        headers=_auth(token_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_history_professional_info_included(client: AsyncClient, db_session: AsyncSession):
    """History item includes professional name, specialty, and CRM."""
    spec = await _seed_specialty(db_session, "pro-info")
    patient, _ = await _seed_patient(db_session, "proinfo@history.com")
    professional = await _seed_professional(db_session, "doc.proinfo@history.com", spec.id)

    await _seed_consult(db_session, patient.id, spec.id, professional.id)

    token = await _login(client, "proinfo@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["professional_name"] is not None
    assert "doc.proinfo@history.com" in item["professional_name"]
    assert item["professional_crm"] is not None
    assert item["professional_specialty"] is not None


@pytest.mark.asyncio
async def test_history_no_professional_fields_null_when_unmatched(client: AsyncClient, db_session: AsyncSession):
    """Unmatched consults have null professional fields."""
    spec = await _seed_specialty(db_session, "no-pro")
    patient, _ = await _seed_patient(db_session, "nopro@history.com")

    await _seed_consult(db_session, patient.id, spec.id, None, ConsultRequestStatus.queued)

    token = await _login(client, "nopro@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["professional_name"] is None
    assert item["professional_crm"] is None
    assert item["professional_specialty"] is None


@pytest.mark.asyncio
async def test_history_document_types(client: AsyncClient, db_session: AsyncSession):
    """History returns both PRESCRIPTION and EXAM_REQUEST documents."""
    spec = await _seed_specialty(db_session, "doc-types")
    patient, _ = await _seed_patient(db_session, "doctypes@history.com")
    professional = await _seed_professional(db_session, "doc.doctypes@history.com", spec.id)

    cr = await _seed_consult(db_session, patient.id, spec.id, professional.id)
    await _seed_document(db_session, cr.id, professional.id, patient.id, DocumentType.PRESCRIPTION, DocumentStatus.SIGNED, "/static/rx.pdf")
    await _seed_document(db_session, cr.id, professional.id, patient.id, DocumentType.EXAM_REQUEST, DocumentStatus.SIGNED, "/static/exam.pdf")

    token = await _login(client, "doctypes@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    docs = resp.json()["items"][0]["documents"]
    doc_types = {d["document_type"] for d in docs}
    assert "PRESCRIPTION" in doc_types
    assert "EXAM_REQUEST" in doc_types


@pytest.mark.asyncio
async def test_history_unauthenticated(client: AsyncClient):
    """Unauthenticated request returns 401."""
    resp = await client.get("/patients/me/history/consults")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_history_no_show(client: AsyncClient, db_session: AsyncSession):
    """no_show_patient consults appear in history with correct status."""
    spec = await _seed_specialty(db_session, "no-show")
    patient, _ = await _seed_patient(db_session, "noshow@history.com")
    professional = await _seed_professional(db_session, "doc.noshow@history.com", spec.id)

    cr = await _seed_consult(db_session, patient.id, spec.id, professional.id, ConsultRequestStatus.no_show_patient)

    token = await _login(client, "noshow@history.com")
    resp = await client.get("/patients/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["status"] == "no_show_patient"
