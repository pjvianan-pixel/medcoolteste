"""Tests for F4 Part 4A: professional financial statement service and endpoints."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole
from app.services.professional_financials import (
    FinancialStatus,
    ProfessionalFinancialSummary,
    ProfessionalTransaction,
    _to_financial_status,
    get_professional_financial_summary,
    list_professional_transactions,
)

# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _register_and_login(
    client: AsyncClient, email: str, role: str
) -> tuple[str, str]:
    resp = await client.post(
        "/auth/register", json={"email": email, "password": "pw", "role": role}
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"], user_id


async def _seed_specialty(db: AsyncSession, slug: str = "clinico-geral") -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


async def _seed_pricing(db: AsyncSession, specialty_id: uuid.UUID) -> SpecialtyPricing:
    pricing = SpecialtyPricing(
        id=uuid.uuid4(),
        specialty_id=specialty_id,
        base_price_cents=14990,
        min_price_cents=9990,
        max_price_cents=24990,
    )
    db.add(pricing)
    await db.commit()
    return pricing


async def _seed_patient(db: AsyncSession, email: str) -> User:
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
    return user


async def _seed_professional(
    db: AsyncSession,
    email: str,
    specialty_id: uuid.UUID,
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
        crm=f"CRM{email[:8]}",
        specialty="general",
        status_verificacao=VerificationStatus.approved,
    )
    db.add(profile)
    db.add(ProfessionalSpecialty(professional_user_id=user.id, specialty_id=specialty_id))
    db.add(
        ProfessionalPresence(
            professional_user_id=user.id,
            is_online=True,
            last_seen_at=datetime.now(tz=UTC),
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_matched_request(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    price_cents: int = 10000,
    scheduled_at: datetime | None = None,
) -> ConsultRequest:
    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_user_id,
        specialty_id=specialty_id,
        quoted_price_cents=price_cents,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.used,
    )
    db.add(quote)
    await db.flush()

    cr = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient_user_id,
        specialty_id=specialty_id,
        quote_id=quote.id,
        complaint="dor de cabeca",
        status=ConsultRequestStatus.matched,
        matched_professional_user_id=professional_user_id,
        scheduled_at=scheduled_at,
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return cr


async def _seed_payment(
    db: AsyncSession,
    consult_request_id: uuid.UUID,
    patient_user_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    amount_cents: int = 10000,
    payment_status: PaymentStatus = PaymentStatus.pending,
    created_at: datetime | None = None,
) -> Payment:
    fee_cents = round(amount_cents * settings.PLATFORM_FEE_PERCENT / 100)
    prof_cents = amount_cents - fee_cents
    payment = Payment(
        id=uuid.uuid4(),
        consult_request_id=consult_request_id,
        patient_user_id=patient_user_id,
        professional_user_id=professional_user_id,
        amount_cents=amount_cents,
        currency="BRL",
        platform_fee_cents=fee_cents,
        professional_amount_cents=prof_cents,
        provider="pagarme",
        status=payment_status,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    # Override created_at if needed (SQLAlchemy server_default makes it hard to set directly)
    if created_at is not None:
        payment.created_at = created_at
        await db.commit()
        await db.refresh(payment)
    return payment


# ── Unit tests: FinancialStatus mapping ──────────────────────────────────────


def test_payment_status_to_financial_paid() -> None:
    assert _to_financial_status(PaymentStatus.paid) == FinancialStatus.paid


def test_payment_status_to_financial_pending() -> None:
    assert _to_financial_status(PaymentStatus.pending) == FinancialStatus.pending
    assert _to_financial_status(PaymentStatus.processing) == FinancialStatus.pending


def test_payment_status_to_financial_refund_pending() -> None:
    assert _to_financial_status(PaymentStatus.refund_pending) == FinancialStatus.refund_pending


def test_payment_status_to_financial_refunded() -> None:
    assert _to_financial_status(PaymentStatus.refunded) == FinancialStatus.refunded


def test_payment_status_to_financial_canceled() -> None:
    assert _to_financial_status(PaymentStatus.failed) == FinancialStatus.canceled
    assert _to_financial_status(PaymentStatus.canceled) == FinancialStatus.canceled


# ── Unit tests: split amounts ─────────────────────────────────────────────────


def test_platform_fee_calculation() -> None:
    """Verify that the stored split matches PLATFORM_FEE_PERCENT."""
    amount = 10000
    expected_fee = round(amount * settings.PLATFORM_FEE_PERCENT / 100)
    expected_professional = amount - expected_fee
    assert expected_fee + expected_professional == amount
    assert expected_fee == 2000
    assert expected_professional == 8000


# ── Service tests: get_professional_financial_summary ─────────────────────────


@pytest.mark.asyncio
async def test_summary_no_payments(db_session: AsyncSession) -> None:
    """Summary for a professional with no payments returns all-zero totals."""
    spec = await _seed_specialty(db_session, "dermatologia")
    prof = await _seed_professional(db_session, "prof.nopaym@test.com", spec.id)

    summary = await get_professional_financial_summary(prof.id, db_session)

    assert isinstance(summary, ProfessionalFinancialSummary)
    assert summary.total_received == 0
    assert summary.total_pending == 0
    assert summary.total_refunded == 0


@pytest.mark.asyncio
async def test_summary_only_pending(db_session: AsyncSession) -> None:
    """Summary with only pending payments returns correct total_pending."""
    spec = await _seed_specialty(db_session, "nutricao")
    patient = await _seed_patient(db_session, "pat.pending@test.com")
    prof = await _seed_professional(db_session, "prof.pending@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.pending)

    summary = await get_professional_financial_summary(prof.id, db_session)

    expected_prof_amount = 10000 - round(10000 * settings.PLATFORM_FEE_PERCENT / 100)
    assert summary.total_received == 0
    assert summary.total_pending == expected_prof_amount
    assert summary.total_refunded == 0


@pytest.mark.asyncio
async def test_summary_paid_pending_refunded(db_session: AsyncSession) -> None:
    """Summary correctly aggregates paid + pending + refunded transactions."""
    spec = await _seed_specialty(db_session, "endocrinologia")
    patient = await _seed_patient(db_session, "pat.mixed@test.com")
    prof = await _seed_professional(db_session, "prof.mixed@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)
    cr3 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 15000)
    cr4 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 5000)

    p1 = await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    p2 = await _seed_payment(db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.pending)
    p3 = await _seed_payment(db_session, cr3.id, patient.id, prof.id, 15000, PaymentStatus.refunded)
    p4 = await _seed_payment(db_session, cr4.id, patient.id, prof.id, 5000, PaymentStatus.refund_pending)

    summary = await get_professional_financial_summary(prof.id, db_session)

    assert summary.total_received == p1.professional_amount_cents
    assert summary.total_pending == p2.professional_amount_cents + p4.professional_amount_cents
    assert summary.total_refunded == p3.professional_amount_cents


@pytest.mark.asyncio
async def test_summary_canceled_payments_excluded(db_session: AsyncSession) -> None:
    """Canceled and failed payments do not appear in any summary total."""
    spec = await _seed_specialty(db_session, "fonoaudiologia")
    patient = await _seed_patient(db_session, "pat.canceled@test.com")
    prof = await _seed_professional(db_session, "prof.canceled@test.com", spec.id)
    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.canceled)
    await _seed_payment(db_session, cr2.id, patient.id, prof.id, 10000, PaymentStatus.failed)

    summary = await get_professional_financial_summary(prof.id, db_session)

    assert summary.total_received == 0
    assert summary.total_pending == 0
    assert summary.total_refunded == 0


@pytest.mark.asyncio
async def test_summary_isolated_between_professionals(db_session: AsyncSession) -> None:
    """Each professional sees only their own payments in the summary."""
    spec = await _seed_specialty(db_session, "fisioterapia")
    patient = await _seed_patient(db_session, "pat.iso@test.com")
    prof1 = await _seed_professional(db_session, "prof1.iso@test.com", spec.id)
    prof2 = await _seed_professional(db_session, "prof2.iso@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof1.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof2.id, 20000)
    pay1 = await _seed_payment(db_session, cr1.id, patient.id, prof1.id, 10000, PaymentStatus.paid)
    await _seed_payment(db_session, cr2.id, patient.id, prof2.id, 20000, PaymentStatus.paid)

    summary1 = await get_professional_financial_summary(prof1.id, db_session)
    summary2 = await get_professional_financial_summary(prof2.id, db_session)

    assert summary1.total_received == pay1.professional_amount_cents
    assert summary2.total_received != summary1.total_received


# ── Service tests: list_professional_transactions ────────────────────────────


@pytest.mark.asyncio
async def test_list_transactions_empty(db_session: AsyncSession) -> None:
    """list_professional_transactions returns empty list for a new professional."""
    spec = await _seed_specialty(db_session, "psicologia")
    prof = await _seed_professional(db_session, "prof.empty@test.com", spec.id)

    items, total = await list_professional_transactions(prof.id, db_session)

    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_transactions_contains_correct_fields(db_session: AsyncSession) -> None:
    """Each transaction item contains the expected fields with correct values."""
    spec = await _seed_specialty(db_session, "neuro")
    patient = await _seed_patient(db_session, "pat.fields@test.com")
    prof = await _seed_professional(db_session, "prof.fields@test.com", spec.id)
    scheduled = datetime.now(tz=UTC) + timedelta(hours=2)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000, scheduled)
    pay = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    items, total = await list_professional_transactions(prof.id, db_session)

    assert total == 1
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ProfessionalTransaction)
    assert item.consult_request_id == cr.id
    assert item.payment_id == pay.id
    assert item.amount_total == 10000
    assert item.platform_fee_amount == pay.platform_fee_cents
    assert item.professional_amount == pay.professional_amount_cents
    assert item.platform_fee_amount + item.professional_amount == item.amount_total
    assert item.financial_status == FinancialStatus.paid


@pytest.mark.asyncio
async def test_list_transactions_pagination(db_session: AsyncSession) -> None:
    """list_professional_transactions respects page and limit parameters."""
    spec = await _seed_specialty(db_session, "geriatria")
    patient = await _seed_patient(db_session, "pat.page@test.com")
    prof = await _seed_professional(db_session, "prof.page@test.com", spec.id)

    # Seed 5 payments
    for i in range(5):
        cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000 + i)
        await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000 + i, PaymentStatus.paid)

    # Page 1 with limit=2
    items_p1, total = await list_professional_transactions(prof.id, db_session, page=1, limit=2)
    assert total == 5
    assert len(items_p1) == 2

    # Page 2 with limit=2
    items_p2, total = await list_professional_transactions(prof.id, db_session, page=2, limit=2)
    assert total == 5
    assert len(items_p2) == 2

    # Page 3 with limit=2 (last page, 1 item)
    items_p3, total = await list_professional_transactions(prof.id, db_session, page=3, limit=2)
    assert total == 5
    assert len(items_p3) == 1

    # No overlap between pages
    ids_p1 = {i.payment_id for i in items_p1}
    ids_p2 = {i.payment_id for i in items_p2}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_list_transactions_filter_by_financial_status(db_session: AsyncSession) -> None:
    """Filtering by financial_status returns only matching transactions."""
    spec = await _seed_specialty(db_session, "hematologia")
    patient = await _seed_patient(db_session, "pat.filt@test.com")
    prof = await _seed_professional(db_session, "prof.filt@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)
    cr3 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 5000)
    pay1 = await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    await _seed_payment(db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.pending)
    await _seed_payment(db_session, cr3.id, patient.id, prof.id, 5000, PaymentStatus.refunded)

    # Filter paid
    paid_items, paid_total = await list_professional_transactions(
        prof.id, db_session, financial_status=FinancialStatus.paid
    )
    assert paid_total == 1
    assert paid_items[0].payment_id == pay1.id

    # Filter pending
    pending_items, pending_total = await list_professional_transactions(
        prof.id, db_session, financial_status=FinancialStatus.pending
    )
    assert pending_total == 1
    assert pending_items[0].financial_status == FinancialStatus.pending

    # Filter refunded
    refunded_items, refunded_total = await list_professional_transactions(
        prof.id, db_session, financial_status=FinancialStatus.refunded
    )
    assert refunded_total == 1
    assert refunded_items[0].financial_status == FinancialStatus.refunded


@pytest.mark.asyncio
async def test_list_transactions_filter_by_date(db_session: AsyncSession) -> None:
    """Filtering by from_date and to_date returns only transactions in the range."""
    spec = await _seed_specialty(db_session, "reumatologia")
    patient = await _seed_patient(db_session, "pat.date@test.com")
    prof = await _seed_professional(db_session, "prof.date@test.com", spec.id)

    now = datetime.now(tz=UTC)
    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)
    await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    await _seed_payment(db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.paid)

    # All payments should appear with a from_date in the past
    items, total = await list_professional_transactions(
        prof.id, db_session, from_date=now - timedelta(hours=1)
    )
    assert total == 2

    # No payments before yesterday
    items_none, total_none = await list_professional_transactions(
        prof.id, db_session, to_date=now - timedelta(days=1)
    )
    assert total_none == 0

    # From far future - no results
    items_future, total_future = await list_professional_transactions(
        prof.id, db_session, from_date=now + timedelta(days=1)
    )
    assert total_future == 0


# ── API endpoint tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_summary_unauthorized(client: AsyncClient) -> None:
    """Unauthenticated request to summary endpoint returns 401."""
    resp = await client.get("/professionals/me/financial/summary")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_transactions_unauthorized(client: AsyncClient) -> None:
    """Unauthenticated request to transactions endpoint returns 401."""
    resp = await client.get("/professionals/me/financial/transactions")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_transactions_invalid_date_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Transactions endpoint returns 422 when from_date is after to_date."""
    spec = await _seed_specialty(db_session, "nefrologiaX")
    prof = await _seed_professional(db_session, "prof.api.daterange@test.com", spec.id)
    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.daterange@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    resp = await client.get(
        "/professionals/me/financial/transactions"
        "?from_date=2025-01-10T00:00:00Z&to_date=2025-01-01T00:00:00Z",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert "from_date" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_api_summary_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """Summary endpoint returns all-zero totals for a professional with no payments."""
    spec = await _seed_specialty(db_session, "anestesiologia")
    await _seed_pricing(db_session, spec.id)
    token, _ = await _register_and_login(client, "prof.api.empty@test.com", "professional")

    # Need to manually create profile for the registered professional
    from sqlalchemy import select
    from app.db.models.user import User
    result = await db_session.execute(
        select(User).where(User.email == "prof.api.empty@test.com")
    )
    user = result.scalar_one()
    db_session.add(
        ProfessionalProfile(
            id=uuid.uuid4(),
            user_id=user.id,
            full_name="Dr Empty",
            crm="CRM999",
            specialty="general",
            status_verificacao=VerificationStatus.approved,
        )
    )
    await db_session.commit()

    resp = await client.get(
        "/professionals/me/financial/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_received"] == 0
    assert data["total_pending"] == 0
    assert data["total_refunded"] == 0


@pytest.mark.asyncio
async def test_api_summary_with_payments(client: AsyncClient, db_session: AsyncSession) -> None:
    """Summary endpoint returns correct totals when payments exist."""
    spec = await _seed_specialty(db_session, "cardiologia2")
    patient = await _seed_patient(db_session, "pat.api.sum@test.com")
    prof = await _seed_professional(db_session, "prof.api.sum@test.com", spec.id)

    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.sum@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)
    pay1 = await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    pay2 = await _seed_payment(db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.pending)

    resp = await client.get(
        "/professionals/me/financial/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_received"] == pay1.professional_amount_cents
    assert data["total_pending"] == pay2.professional_amount_cents
    assert data["total_refunded"] == 0


@pytest.mark.asyncio
async def test_api_summary_refunded(client: AsyncClient, db_session: AsyncSession) -> None:
    """Summary correctly shows total_refunded for refunded payments."""
    spec = await _seed_specialty(db_session, "oncologia")
    patient = await _seed_patient(db_session, "pat.api.ref@test.com")
    prof = await _seed_professional(db_session, "prof.api.ref@test.com", spec.id)

    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.ref@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    pay = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.refunded)

    resp = await client.get(
        "/professionals/me/financial/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_received"] == 0
    assert data["total_pending"] == 0
    assert data["total_refunded"] == pay.professional_amount_cents


@pytest.mark.asyncio
async def test_api_transactions_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """Transactions endpoint returns empty list for a professional with no payments."""
    spec = await _seed_specialty(db_session, "infectologia")
    prof = await _seed_professional(db_session, "prof.api.tx.empty@test.com", spec.id)
    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.tx.empty@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    resp = await client.get(
        "/professionals/me/financial/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["limit"] == 20


@pytest.mark.asyncio
async def test_api_transactions_pagination(client: AsyncClient, db_session: AsyncSession) -> None:
    """Transactions endpoint supports page and limit query parameters."""
    spec = await _seed_specialty(db_session, "pneumologia")
    patient = await _seed_patient(db_session, "pat.api.pg@test.com")
    prof = await _seed_professional(db_session, "prof.api.pg@test.com", spec.id)
    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.pg@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    for i in range(5):
        cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000 + i)
        await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000 + i, PaymentStatus.paid)

    resp = await client.get(
        "/professionals/me/financial/transactions?page=1&limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_api_transactions_filter_by_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Transactions endpoint supports filtering by financial_status."""
    spec = await _seed_specialty(db_session, "urologia")
    patient = await _seed_patient(db_session, "pat.api.fs@test.com")
    prof = await _seed_professional(db_session, "prof.api.fs@test.com", spec.id)
    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.fs@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)
    await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    await _seed_payment(db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.pending)

    resp_paid = await client.get(
        "/professionals/me/financial/transactions?financial_status=paid",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_paid.status_code == 200, resp_paid.text
    assert resp_paid.json()["total"] == 1
    assert resp_paid.json()["items"][0]["financial_status"] == "paid"

    resp_pending = await client.get(
        "/professionals/me/financial/transactions?financial_status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_pending.status_code == 200, resp_pending.text
    assert resp_pending.json()["total"] == 1
    assert resp_pending.json()["items"][0]["financial_status"] == "pending"


@pytest.mark.asyncio
async def test_api_transactions_item_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Each transaction item in the API response has the expected fields."""
    spec = await _seed_specialty(db_session, "gastroenterologia")
    patient = await _seed_patient(db_session, "pat.api.item@test.com")
    prof = await _seed_professional(db_session, "prof.api.item@test.com", spec.id)
    token_resp = await client.post(
        "/auth/login", json={"email": "prof.api.item@test.com", "password": "pw"}
    )
    token = token_resp.json()["access_token"]

    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    pay = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    resp = await client.get(
        "/professionals/me/financial/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["consult_request_id"] == str(cr.id)
    assert item["payment_id"] == str(pay.id)
    assert item["amount_total"] == 10000
    assert item["platform_fee_amount"] == pay.platform_fee_cents
    assert item["professional_amount"] == pay.professional_amount_cents
    assert item["platform_fee_amount"] + item["professional_amount"] == item["amount_total"]
    assert item["financial_status"] == "paid"
    assert "created_at" in item


@pytest.mark.asyncio
async def test_api_patient_cannot_access_financial_endpoints(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient token cannot access professional financial endpoints."""
    spec = await _seed_specialty(db_session, "alergologia")
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "pat.role@test.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(
        PatientProfile(
            id=uuid.uuid4(), user_id=patient_uuid, full_name="Test", cpf=cpf
        )
    )
    await db_session.commit()

    resp_sum = await client.get(
        "/professionals/me/financial/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_sum.status_code == 403

    resp_tx = await client.get(
        "/professionals/me/financial/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_tx.status_code == 403
