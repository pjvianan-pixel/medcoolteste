"""Tests for F6 Part 2 – Professional Consult History."""
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
from app.db.models.professional_payout import ProfessionalPayout
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole

# ── Seed helpers ───────────────────────────────────────────────────────────────


async def _seed_specialty(db: AsyncSession, slug: str) -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


async def _seed_patient(db: AsyncSession, email: str, name: str = "Patient User") -> tuple[User, PatientProfile]:
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
        id=uuid.uuid4(), user_id=user.id, full_name=name, cpf=cpf
    )
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    return user, profile


async def _seed_professional(db: AsyncSession, email: str) -> User:
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
    professional_id: uuid.UUID,
    specialty_id: uuid.UUID,
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
        complaint="headache",
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
    professional_id: uuid.UUID,
    pay_status: PaymentStatus = PaymentStatus.paid,
    amount_cents: int = 10000,
    payout_id: uuid.UUID | None = None,
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
        payout_id=payout_id,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


async def _seed_payout(
    db: AsyncSession,
    professional_id: uuid.UUID,
    total_cents: int = 9000,
) -> ProfessionalPayout:
    payout = ProfessionalPayout(
        id=uuid.uuid4(),
        professional_user_id=professional_id,
        total_amount_cents=total_cents,
        paid_out_at=datetime.now(tz=UTC),
    )
    db.add(payout)
    await db.commit()
    await db.refresh(payout)
    return payout


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
async def test_pro_history_empty(client: AsyncClient, db_session: AsyncSession):
    """Professional with no consults gets an empty history."""
    pro = await _seed_professional(db_session, "empty@prohist.com")
    token = await _login(client, "empty@prohist.com")

    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1
    assert data["limit"] == 20


@pytest.mark.asyncio
async def test_pro_history_multiple_consults(client: AsyncClient, db_session: AsyncSession):
    """History returns all consults for the professional in various states."""
    spec = await _seed_specialty(db_session, "pro-multi")
    patient, _ = await _seed_patient(db_session, "patient.multi@prohist.com")
    pro = await _seed_professional(db_session, "pro.multi@prohist.com")

    cr1 = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.matched)
    cr2 = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.cancelled_by_professional)
    cr3 = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.no_show_patient)

    token = await _login(client, "pro.multi@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    ids = {item["consult_id"] for item in data["items"]}
    assert str(cr1.id) in ids
    assert str(cr2.id) in ids
    assert str(cr3.id) in ids


@pytest.mark.asyncio
async def test_pro_history_with_payment(client: AsyncClient, db_session: AsyncSession):
    """History includes payment summary when payment exists."""
    spec = await _seed_specialty(db_session, "pro-pay")
    patient, _ = await _seed_patient(db_session, "patient.pay@prohist.com")
    pro = await _seed_professional(db_session, "pro.pay@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr.id, patient.id, pro.id, PaymentStatus.paid, 10000)

    token = await _login(client, "pro.pay@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    payment = resp.json()["items"][0]["payment"]
    assert payment is not None
    assert payment["status"] == "paid"
    assert payment["financial_status"] == "paid"
    assert payment["amount_total_cents"] == 10000
    assert payment["professional_amount_cents"] == 9000
    assert payment["platform_fee_cents"] == 1000
    assert payment["refunded_amount_cents"] == 0


@pytest.mark.asyncio
async def test_pro_history_refunded_payment(client: AsyncClient, db_session: AsyncSession):
    """History shows refunded_amount_cents populated when payment is refunded."""
    spec = await _seed_specialty(db_session, "pro-refund")
    patient, _ = await _seed_patient(db_session, "patient.refund@prohist.com")
    pro = await _seed_professional(db_session, "pro.refund@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.cancelled_by_patient)
    await _seed_payment(db_session, cr.id, patient.id, pro.id, PaymentStatus.refunded, 8000)

    token = await _login(client, "pro.refund@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    payment = resp.json()["items"][0]["payment"]
    assert payment["status"] == "refunded"
    assert payment["financial_status"] == "refunded"
    assert payment["refunded_amount_cents"] == 8000


@pytest.mark.asyncio
async def test_pro_history_no_payment(client: AsyncClient, db_session: AsyncSession):
    """History items without a payment have payment=null and payout=null."""
    spec = await _seed_specialty(db_session, "pro-nopay")
    patient, _ = await _seed_patient(db_session, "patient.nopay@prohist.com")
    pro = await _seed_professional(db_session, "pro.nopay@prohist.com")

    await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.cancelled_by_professional)

    token = await _login(client, "pro.nopay@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["payment"] is None
    assert item["payout"] is None


@pytest.mark.asyncio
async def test_pro_history_with_payout(client: AsyncClient, db_session: AsyncSession):
    """History includes payout summary when payment has a payout_id."""
    spec = await _seed_specialty(db_session, "pro-payout")
    patient, _ = await _seed_patient(db_session, "patient.payout@prohist.com")
    pro = await _seed_professional(db_session, "pro.payout@prohist.com")

    payout = await _seed_payout(db_session, pro.id)
    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr.id, patient.id, pro.id, PaymentStatus.paid, payout_id=payout.id)

    token = await _login(client, "pro.payout@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["payout"] is not None
    assert item["payout"]["payout_id"] == str(payout.id)
    assert item["payout"]["paid_out_at"] is not None


@pytest.mark.asyncio
async def test_pro_history_payment_no_payout(client: AsyncClient, db_session: AsyncSession):
    """History shows payout=null when payment has no payout_id."""
    spec = await _seed_specialty(db_session, "pro-nopayout")
    patient, _ = await _seed_patient(db_session, "patient.nopayout@prohist.com")
    pro = await _seed_professional(db_session, "pro.nopayout@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr.id, patient.id, pro.id, PaymentStatus.paid, payout_id=None)

    token = await _login(client, "pro.nopayout@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["payment"] is not None
    assert item["payout"] is None


@pytest.mark.asyncio
async def test_pro_history_signed_document_has_file_url(client: AsyncClient, db_session: AsyncSession):
    """Signed documents include file_url; draft documents have file_url=null."""
    spec = await _seed_specialty(db_session, "pro-signed-doc")
    patient, _ = await _seed_patient(db_session, "patient.signeddoc@prohist.com")
    pro = await _seed_professional(db_session, "pro.signeddoc@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_document(
        db_session, cr.id, pro.id, patient.id,
        doc_type=DocumentType.PRESCRIPTION,
        doc_status=DocumentStatus.SIGNED,
        file_url="/static/documents/rx.pdf",
    )
    await _seed_document(
        db_session, cr.id, pro.id, patient.id,
        doc_type=DocumentType.EXAM_REQUEST,
        doc_status=DocumentStatus.DRAFT,
    )

    token = await _login(client, "pro.signeddoc@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    docs = resp.json()["items"][0]["documents"]
    assert len(docs) == 2

    signed = next(d for d in docs if d["status"] == "SIGNED")
    draft = next(d for d in docs if d["status"] == "DRAFT")

    assert signed["file_url"] == "/static/documents/rx.pdf"
    assert draft["file_url"] is None


@pytest.mark.asyncio
async def test_pro_history_no_documents(client: AsyncClient, db_session: AsyncSession):
    """Consults without documents have an empty documents list."""
    spec = await _seed_specialty(db_session, "pro-nodocs")
    patient, _ = await _seed_patient(db_session, "patient.nodocs@prohist.com")
    pro = await _seed_professional(db_session, "pro.nodocs@prohist.com")

    await _seed_consult(db_session, patient.id, pro.id, spec.id)

    token = await _login(client, "pro.nodocs@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"][0]["documents"] == []


@pytest.mark.asyncio
async def test_pro_history_patient_name_included(client: AsyncClient, db_session: AsyncSession):
    """History item includes the patient's name."""
    spec = await _seed_specialty(db_session, "pro-patname")
    patient, _ = await _seed_patient(db_session, "patient.patname@prohist.com", name="João Silva")
    pro = await _seed_professional(db_session, "pro.patname@prohist.com")

    await _seed_consult(db_session, patient.id, pro.id, spec.id)

    token = await _login(client, "pro.patname@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"][0]["patient_name"] == "João Silva"


@pytest.mark.asyncio
async def test_pro_history_filter_consult_status(client: AsyncClient, db_session: AsyncSession):
    """consult_status filter returns only matching consults."""
    spec = await _seed_specialty(db_session, "pro-statusflt")
    patient, _ = await _seed_patient(db_session, "patient.sflt@prohist.com")
    pro = await _seed_professional(db_session, "pro.sflt@prohist.com")

    await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.matched)
    cr_cancelled = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.cancelled_by_patient)

    token = await _login(client, "pro.sflt@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults?consult_status=cancelled_by_patient",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_cancelled.id)


@pytest.mark.asyncio
async def test_pro_history_filter_invalid_consult_status(client: AsyncClient, db_session: AsyncSession):
    """Invalid consult_status returns 422."""
    pro = await _seed_professional(db_session, "pro.badstatus@prohist.com")
    token = await _login(client, "pro.badstatus@prohist.com")

    resp = await client.get(
        "/professionals/me/history/consults?consult_status=nonexistent",
        headers=_auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pro_history_filter_payment_status(client: AsyncClient, db_session: AsyncSession):
    """payment_status filter returns only consults with matching financial status."""
    spec = await _seed_specialty(db_session, "pro-payflt")
    patient, _ = await _seed_patient(db_session, "patient.payflt@prohist.com")
    pro = await _seed_professional(db_session, "pro.payflt@prohist.com")

    cr_paid = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr_paid.id, patient.id, pro.id, PaymentStatus.paid)

    cr_refund = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.cancelled_by_patient)
    await _seed_payment(db_session, cr_refund.id, patient.id, pro.id, PaymentStatus.refunded)

    token = await _login(client, "pro.payflt@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults?payment_status=paid",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_paid.id)


@pytest.mark.asyncio
async def test_pro_history_filter_invalid_payment_status(client: AsyncClient, db_session: AsyncSession):
    """Invalid payment_status returns 422."""
    pro = await _seed_professional(db_session, "pro.badpay@prohist.com")
    token = await _login(client, "pro.badpay@prohist.com")

    resp = await client.get(
        "/professionals/me/history/consults?payment_status=nonexistent",
        headers=_auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pro_history_filter_has_payout_true(client: AsyncClient, db_session: AsyncSession):
    """has_payout=true returns only consults whose payment has a payout_id."""
    spec = await _seed_specialty(db_session, "pro-haspayout")
    patient, _ = await _seed_patient(db_session, "patient.hpout@prohist.com")
    pro = await _seed_professional(db_session, "pro.hpout@prohist.com")

    payout = await _seed_payout(db_session, pro.id)

    cr_with = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr_with.id, patient.id, pro.id, payout_id=payout.id)

    cr_without = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr_without.id, patient.id, pro.id, payout_id=None)

    token = await _login(client, "pro.hpout@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults?has_payout=true",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_with.id)


@pytest.mark.asyncio
async def test_pro_history_filter_has_payout_false(client: AsyncClient, db_session: AsyncSession):
    """has_payout=false returns only consults without a payout_id."""
    spec = await _seed_specialty(db_session, "pro-nopayout2")
    patient, _ = await _seed_patient(db_session, "patient.npout2@prohist.com")
    pro = await _seed_professional(db_session, "pro.npout2@prohist.com")

    payout = await _seed_payout(db_session, pro.id)

    cr_with = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr_with.id, patient.id, pro.id, payout_id=payout.id)

    cr_without = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr_without.id, patient.id, pro.id, payout_id=None)

    token = await _login(client, "pro.npout2@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults?has_payout=false",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_without.id)


@pytest.mark.asyncio
async def test_pro_history_filter_from_date(client: AsyncClient, db_session: AsyncSession):
    """from_date filter excludes consults created before that date."""
    spec = await _seed_specialty(db_session, "pro-fromdate")
    patient, _ = await _seed_patient(db_session, "patient.fromdt@prohist.com")
    pro = await _seed_professional(db_session, "pro.fromdt@prohist.com")

    old_date = datetime.now(tz=UTC) - timedelta(days=10)
    recent_date = datetime.now(tz=UTC) - timedelta(days=1)

    cr_old = await _seed_consult(db_session, patient.id, pro.id, spec.id, created_at=old_date)
    cr_recent = await _seed_consult(db_session, patient.id, pro.id, spec.id, created_at=recent_date)

    cutoff = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat()
    token = await _login(client, "pro.fromdt@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults",
        params={"from_date": cutoff},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    ids = {item["consult_id"] for item in resp.json()["items"]}
    assert str(cr_recent.id) in ids
    assert str(cr_old.id) not in ids


@pytest.mark.asyncio
async def test_pro_history_filter_to_date(client: AsyncClient, db_session: AsyncSession):
    """to_date filter excludes consults created after that date."""
    spec = await _seed_specialty(db_session, "pro-todate")
    patient, _ = await _seed_patient(db_session, "patient.todt@prohist.com")
    pro = await _seed_professional(db_session, "pro.todt@prohist.com")

    old_date = datetime.now(tz=UTC) - timedelta(days=10)
    recent_date = datetime.now(tz=UTC) - timedelta(days=1)

    cr_old = await _seed_consult(db_session, patient.id, pro.id, spec.id, created_at=old_date)
    cr_recent = await _seed_consult(db_session, patient.id, pro.id, spec.id, created_at=recent_date)

    cutoff = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat()
    token = await _login(client, "pro.todt@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults",
        params={"to_date": cutoff},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    ids = {item["consult_id"] for item in resp.json()["items"]}
    assert str(cr_old.id) in ids
    assert str(cr_recent.id) not in ids


@pytest.mark.asyncio
async def test_pro_history_filter_patient_name(client: AsyncClient, db_session: AsyncSession):
    """patient_name filter returns only consults whose patient name contains the substring."""
    spec = await _seed_specialty(db_session, "pro-patflt")
    patient_a, _ = await _seed_patient(db_session, "patient.patflt_a@prohist.com", name="Maria Souza")
    patient_b, _ = await _seed_patient(db_session, "patient.patflt_b@prohist.com", name="Carlos Lima")
    pro = await _seed_professional(db_session, "pro.patflt@prohist.com")

    cr_a = await _seed_consult(db_session, patient_a.id, pro.id, spec.id)
    cr_b = await _seed_consult(db_session, patient_b.id, pro.id, spec.id)

    token = await _login(client, "pro.patflt@prohist.com")
    resp = await client.get(
        "/professionals/me/history/consults?patient_name=maria",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["consult_id"] == str(cr_a.id)


@pytest.mark.asyncio
async def test_pro_history_filter_patient_name_case_insensitive(client: AsyncClient, db_session: AsyncSession):
    """patient_name filter is case-insensitive."""
    spec = await _seed_specialty(db_session, "pro-patcase")
    patient, _ = await _seed_patient(db_session, "patient.patcase@prohist.com", name="Ana Oliveira")
    pro = await _seed_professional(db_session, "pro.patcase@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)

    token = await _login(client, "pro.patcase@prohist.com")
    for query in ["ANA", "ana", "Ana", "OLIVEIRA"]:
        resp = await client.get(
            f"/professionals/me/history/consults?patient_name={query}",
            headers=_auth(token),
        )
        assert resp.status_code == 200, f"Failed for query: {query}"
        assert resp.json()["total"] == 1, f"Expected 1 result for query: {query}"


@pytest.mark.asyncio
async def test_pro_history_pagination(client: AsyncClient, db_session: AsyncSession):
    """Pagination returns correct page and limit."""
    spec = await _seed_specialty(db_session, "pro-pagination")
    patient, _ = await _seed_patient(db_session, "patient.page@prohist.com")
    pro = await _seed_professional(db_session, "pro.page@prohist.com")

    for _ in range(5):
        await _seed_consult(db_session, patient.id, pro.id, spec.id)

    token = await _login(client, "pro.page@prohist.com")

    # Page 1, limit 2
    resp = await client.get(
        "/professionals/me/history/consults?page=1&limit=2",
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
        "/professionals/me/history/consults?page=3&limit=2",
        headers=_auth(token),
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 5
    assert len(data2["items"]) == 1

    # Page 4, limit 2 → empty
    resp3 = await client.get(
        "/professionals/me/history/consults?page=4&limit=2",
        headers=_auth(token),
    )
    assert resp3.status_code == 200
    assert resp3.json()["items"] == []


@pytest.mark.asyncio
async def test_pro_history_isolation_between_professionals(client: AsyncClient, db_session: AsyncSession):
    """A professional cannot see another professional's consults."""
    spec = await _seed_specialty(db_session, "pro-isolation")
    patient, _ = await _seed_patient(db_session, "patient.iso@prohist.com")
    pro_a = await _seed_professional(db_session, "pro.iso_a@prohist.com")
    pro_b = await _seed_professional(db_session, "pro.iso_b@prohist.com")

    cr_a = await _seed_consult(db_session, patient.id, pro_a.id, spec.id)

    token_b = await _login(client, "pro.iso_b@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token_b))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    ids = {item["consult_id"] for item in data["items"]}
    assert str(cr_a.id) not in ids


@pytest.mark.asyncio
async def test_pro_history_unauthenticated(client: AsyncClient):
    """Unauthenticated request returns 401."""
    resp = await client.get("/professionals/me/history/consults")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pro_history_detail_found(client: AsyncClient, db_session: AsyncSession):
    """GET /professionals/me/history/consults/{id} returns the consult item."""
    spec = await _seed_specialty(db_session, "pro-detail")
    patient, _ = await _seed_patient(db_session, "patient.detail@prohist.com", name="Detalhe Paciente")
    pro = await _seed_professional(db_session, "pro.detail@prohist.com")

    payout = await _seed_payout(db_session, pro.id)
    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_payment(db_session, cr.id, patient.id, pro.id, PaymentStatus.paid, payout_id=payout.id)
    await _seed_document(db_session, cr.id, pro.id, patient.id, DocumentType.PRESCRIPTION, DocumentStatus.SIGNED, "/static/rx.pdf")

    token = await _login(client, "pro.detail@prohist.com")
    resp = await client.get(f"/professionals/me/history/consults/{cr.id}", headers=_auth(token))
    assert resp.status_code == 200
    item = resp.json()
    assert item["consult_id"] == str(cr.id)
    assert item["patient_name"] == "Detalhe Paciente"
    assert item["payment"] is not None
    assert item["payout"] is not None
    assert len(item["documents"]) == 1
    assert item["documents"][0]["file_url"] == "/static/rx.pdf"


@pytest.mark.asyncio
async def test_pro_history_detail_not_found(client: AsyncClient, db_session: AsyncSession):
    """GET /professionals/me/history/consults/{id} returns 404 for unknown ID."""
    pro = await _seed_professional(db_session, "pro.notfound@prohist.com")
    token = await _login(client, "pro.notfound@prohist.com")

    resp = await client.get(
        f"/professionals/me/history/consults/{uuid.uuid4()}",
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pro_history_detail_forbidden_other_professional(client: AsyncClient, db_session: AsyncSession):
    """GET /professionals/me/history/consults/{id} returns 404 for another professional's consult."""
    spec = await _seed_specialty(db_session, "pro-det-forbidden")
    patient, _ = await _seed_patient(db_session, "patient.detforbid@prohist.com")
    pro_a = await _seed_professional(db_session, "pro.detforbid_a@prohist.com")
    pro_b = await _seed_professional(db_session, "pro.detforbid_b@prohist.com")

    cr_a = await _seed_consult(db_session, patient.id, pro_a.id, spec.id)

    token_b = await _login(client, "pro.detforbid_b@prohist.com")
    resp = await client.get(
        f"/professionals/me/history/consults/{cr_a.id}",
        headers=_auth(token_b),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pro_history_no_show_status(client: AsyncClient, db_session: AsyncSession):
    """no_show_patient consults appear in history with correct status."""
    spec = await _seed_specialty(db_session, "pro-noshow")
    patient, _ = await _seed_patient(db_session, "patient.noshow@prohist.com")
    pro = await _seed_professional(db_session, "pro.noshow@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id, ConsultRequestStatus.no_show_patient)

    token = await _login(client, "pro.noshow@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["items"][0]["status"] == "no_show_patient"


@pytest.mark.asyncio
async def test_pro_history_document_types(client: AsyncClient, db_session: AsyncSession):
    """History returns both PRESCRIPTION and EXAM_REQUEST documents."""
    spec = await _seed_specialty(db_session, "pro-doctypes")
    patient, _ = await _seed_patient(db_session, "patient.doctypes@prohist.com")
    pro = await _seed_professional(db_session, "pro.doctypes@prohist.com")

    cr = await _seed_consult(db_session, patient.id, pro.id, spec.id)
    await _seed_document(db_session, cr.id, pro.id, patient.id, DocumentType.PRESCRIPTION, DocumentStatus.SIGNED, "/static/rx.pdf")
    await _seed_document(db_session, cr.id, pro.id, patient.id, DocumentType.EXAM_REQUEST, DocumentStatus.SIGNED, "/static/exam.pdf")

    token = await _login(client, "pro.doctypes@prohist.com")
    resp = await client.get("/professionals/me/history/consults", headers=_auth(token))
    assert resp.status_code == 200
    docs = resp.json()["items"][0]["documents"]
    doc_types = {d["document_type"] for d in docs}
    assert "PRESCRIPTION" in doc_types
    assert "EXAM_REQUEST" in doc_types
