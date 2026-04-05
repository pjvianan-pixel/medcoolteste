"""Tests for F4 Part 4B: admin financial endpoints and payout service."""

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
from app.services.admin_financials import (
    AdminFinancialSummary,
    AdminFinancialTransaction,
    create_payouts,
    get_admin_financial_summary,
    list_admin_financial_transactions,
)
from app.services.professional_financials import FinancialStatus

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


async def _seed_admin(db: AsyncSession, email: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hash_password("pw"),
        role=UserRole.admin,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_specialty(db: AsyncSession, slug: str = "clinico-geral") -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


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
    if created_at is not None:
        payment.created_at = created_at
        await db.commit()
        await db.refresh(payment)
    return payment


async def _admin_token(client: AsyncClient, db: AsyncSession, email: str) -> str:
    """Create an admin user and return their JWT token."""
    await _seed_admin(db, email)
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Service tests: get_admin_financial_summary ────────────────────────────────


@pytest.mark.asyncio
async def test_admin_summary_empty(db_session: AsyncSession) -> None:
    """Summary with no payments returns all-zero totals."""
    summary = await get_admin_financial_summary(db_session)
    assert isinstance(summary, AdminFinancialSummary)
    assert summary.total_payments_cents == 0
    assert summary.total_platform_fees_cents == 0
    assert summary.total_professional_amount_cents == 0
    assert summary.total_refunded_cents == 0


@pytest.mark.asyncio
async def test_admin_summary_paid_only(db_session: AsyncSession) -> None:
    """Summary with only paid payments aggregates correctly."""
    spec = await _seed_specialty(db_session, "admin-sum-paid")
    patient = await _seed_patient(db_session, "pat.admsump@test.com")
    prof = await _seed_professional(db_session, "pro.admsump@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    summary = await get_admin_financial_summary(db_session)

    assert summary.total_payments_cents == p.amount_cents
    assert summary.total_platform_fees_cents == p.platform_fee_cents
    assert summary.total_professional_amount_cents == p.professional_amount_cents
    assert summary.total_refunded_cents == 0


@pytest.mark.asyncio
async def test_admin_summary_mixed_statuses(db_session: AsyncSession) -> None:
    """Summary with paid + refunded + pending payments."""
    spec = await _seed_specialty(db_session, "admin-sum-mix")
    patient = await _seed_patient(db_session, "pat.admsmix@test.com")
    prof = await _seed_professional(db_session, "pro.admsmix@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)
    cr3 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 5000)

    p_paid = await _seed_payment(
        db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid
    )
    p_refunded = await _seed_payment(
        db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.refunded
    )
    # pending payment should not appear in any summary total except being ignored
    await _seed_payment(
        db_session, cr3.id, patient.id, prof.id, 5000, PaymentStatus.pending
    )

    summary = await get_admin_financial_summary(db_session)

    assert summary.total_payments_cents == p_paid.amount_cents
    assert summary.total_platform_fees_cents == p_paid.platform_fee_cents
    assert summary.total_professional_amount_cents == p_paid.professional_amount_cents
    assert summary.total_refunded_cents == p_refunded.amount_cents


@pytest.mark.asyncio
async def test_admin_summary_date_filter(db_session: AsyncSession) -> None:
    """Date filters restrict the summary to a specific window."""
    spec = await _seed_specialty(db_session, "admin-sum-date")
    patient = await _seed_patient(db_session, "pat.admsdate@test.com")
    prof = await _seed_professional(db_session, "pro.admsdate@test.com", spec.id)

    old_date = datetime(2025, 1, 1, tzinfo=UTC)
    new_date = datetime(2026, 1, 1, tzinfo=UTC)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)

    await _seed_payment(
        db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid, created_at=old_date
    )
    p_new = await _seed_payment(
        db_session, cr2.id, patient.id, prof.id, 10000, PaymentStatus.paid, created_at=new_date
    )

    summary = await get_admin_financial_summary(
        db_session, from_date=datetime(2025, 6, 1, tzinfo=UTC)
    )
    assert summary.total_payments_cents == p_new.amount_cents


# ── Service tests: list_admin_financial_transactions ─────────────────────────


@pytest.mark.asyncio
async def test_list_transactions_empty(db_session: AsyncSession) -> None:
    items, total = await list_admin_financial_transactions(db_session)
    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_list_transactions_basic(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "admin-tx-basic")
    patient = await _seed_patient(db_session, "pat.admtxb@test.com")
    prof = await _seed_professional(db_session, "pro.admtxb@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    items, total = await list_admin_financial_transactions(db_session)

    assert total == 1
    assert len(items) == 1
    tx = items[0]
    assert isinstance(tx, AdminFinancialTransaction)
    assert tx.payment_id == p.id
    assert tx.consult_request_id == cr.id
    assert tx.patient_user_id == patient.id
    assert tx.professional_user_id == prof.id
    assert tx.amount_total_cents == p.amount_cents
    assert tx.financial_status == FinancialStatus.paid
    assert tx.payout_id is None


@pytest.mark.asyncio
async def test_list_transactions_filter_by_status(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "admin-tx-status")
    patient = await _seed_patient(db_session, "pat.admtxst@test.com")
    prof = await _seed_professional(db_session, "pro.admtxst@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)

    await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    await _seed_payment(db_session, cr2.id, patient.id, prof.id, 10000, PaymentStatus.pending)

    paid_items, paid_total = await list_admin_financial_transactions(
        db_session, financial_status=FinancialStatus.paid
    )
    assert paid_total == 1
    assert paid_items[0].financial_status == FinancialStatus.paid


@pytest.mark.asyncio
async def test_list_transactions_filter_by_professional(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "admin-tx-prof")
    patient = await _seed_patient(db_session, "pat.admtxpf@test.com")
    prof1 = await _seed_professional(db_session, "pro1.admtxpf@test.com", spec.id)
    prof2 = await _seed_professional(db_session, "pro2.admtxpf@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof1.id)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof2.id)

    await _seed_payment(db_session, cr1.id, patient.id, prof1.id)
    await _seed_payment(db_session, cr2.id, patient.id, prof2.id)

    items, total = await list_admin_financial_transactions(
        db_session, professional_user_id=prof1.id
    )
    assert total == 1
    assert items[0].professional_user_id == prof1.id


@pytest.mark.asyncio
async def test_list_transactions_pagination(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "admin-tx-page")
    patient = await _seed_patient(db_session, "pat.admtxpg@test.com")
    prof = await _seed_professional(db_session, "pro.admtxpg@test.com", spec.id)

    for _ in range(5):
        cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)
        await _seed_payment(db_session, cr.id, patient.id, prof.id)

    items_p1, total = await list_admin_financial_transactions(db_session, page=1, limit=3)
    items_p2, _ = await list_admin_financial_transactions(db_session, page=2, limit=3)

    assert total == 5
    assert len(items_p1) == 3
    assert len(items_p2) == 2


@pytest.mark.asyncio
async def test_list_transactions_date_filter(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "admin-tx-date")
    patient = await _seed_patient(db_session, "pat.admtxdt@test.com")
    prof = await _seed_professional(db_session, "pro.admtxdt@test.com", spec.id)

    old = datetime(2025, 1, 1, tzinfo=UTC)
    new = datetime(2026, 1, 1, tzinfo=UTC)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)

    await _seed_payment(
        db_session, cr1.id, patient.id, prof.id, created_at=old
    )
    await _seed_payment(
        db_session, cr2.id, patient.id, prof.id, created_at=new
    )

    items, total = await list_admin_financial_transactions(
        db_session, from_date=datetime(2025, 6, 1, tzinfo=UTC)
    )
    assert total == 1


# ── Service tests: create_payouts ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_payouts_basic(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "payout-basic")
    patient = await _seed_patient(db_session, "pat.pybas@test.com")
    prof = await _seed_professional(db_session, "pro.pybas@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    result = await create_payouts([p.id], db_session)

    assert result.payouts_created == 1
    assert result.payments_included == 1
    assert result.already_paid == 0
    assert len(result.professional_summaries) == 1
    s = result.professional_summaries[0]
    assert s.professional_user_id == prof.id
    assert s.total_professional_amount_cents == p.professional_amount_cents
    assert s.payment_count == 1

    # Verify DB state
    await db_session.refresh(p)
    assert p.payout_id == s.payout_id


@pytest.mark.asyncio
async def test_create_payouts_multiple_professionals(db_session: AsyncSession) -> None:
    """Payments for different professionals create separate payouts."""
    spec = await _seed_specialty(db_session, "payout-multi")
    patient = await _seed_patient(db_session, "pat.pymulti@test.com")
    prof1 = await _seed_professional(db_session, "pro1.pymulti@test.com", spec.id)
    prof2 = await _seed_professional(db_session, "pro2.pymulti@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof1.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof2.id, 20000)

    p1 = await _seed_payment(db_session, cr1.id, patient.id, prof1.id, 10000, PaymentStatus.paid)
    p2 = await _seed_payment(db_session, cr2.id, patient.id, prof2.id, 20000, PaymentStatus.paid)

    result = await create_payouts([p1.id, p2.id], db_session)

    assert result.payouts_created == 2
    assert result.payments_included == 2
    prof_ids = {s.professional_user_id for s in result.professional_summaries}
    assert prof_ids == {prof1.id, prof2.id}


@pytest.mark.asyncio
async def test_create_payouts_multiple_payments_same_prof(db_session: AsyncSession) -> None:
    """Multiple payments for the same professional produce one payout."""
    spec = await _seed_specialty(db_session, "payout-same")
    patient = await _seed_patient(db_session, "pat.pysame@test.com")
    prof = await _seed_professional(db_session, "pro.pysame@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 20000)

    p1 = await _seed_payment(db_session, cr1.id, patient.id, prof.id, 10000, PaymentStatus.paid)
    p2 = await _seed_payment(db_session, cr2.id, patient.id, prof.id, 20000, PaymentStatus.paid)

    result = await create_payouts([p1.id, p2.id], db_session)

    assert result.payouts_created == 1
    assert result.payments_included == 2
    s = result.professional_summaries[0]
    assert s.total_professional_amount_cents == (
        p1.professional_amount_cents + p2.professional_amount_cents
    )
    assert s.payment_count == 2


@pytest.mark.asyncio
async def test_create_payouts_idempotent(db_session: AsyncSession) -> None:
    """Payments already linked to a payout are silently skipped."""
    spec = await _seed_specialty(db_session, "payout-idemp")
    patient = await _seed_patient(db_session, "pat.pyidemp@test.com")
    prof = await _seed_professional(db_session, "pro.pyidemp@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    # First payout
    first = await create_payouts([p.id], db_session)
    assert first.payouts_created == 1
    first_payout_id = first.professional_summaries[0].payout_id

    # Second payout with same payment – should be skipped
    second = await create_payouts([p.id], db_session)
    assert second.payouts_created == 0
    assert second.payments_included == 0
    assert second.already_paid == 1

    # payout_id must not have changed
    await db_session.refresh(p)
    assert p.payout_id == first_payout_id


@pytest.mark.asyncio
async def test_create_payouts_empty_list(db_session: AsyncSession) -> None:
    result = await create_payouts([], db_session)
    assert result.payouts_created == 0
    assert result.payments_included == 0
    assert result.already_paid == 0


# ── HTTP endpoint tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_summary_endpoint_ok(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _admin_token(client, db_session, "admin.sumok@test.com")
    spec = await _seed_specialty(db_session, "ep-sum-ok")
    patient = await _seed_patient(db_session, "pat.epsumok@test.com")
    prof = await _seed_professional(db_session, "pro.epsumok@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    resp = await client.get(
        "/admin/financial/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_payments_cents"] == p.amount_cents
    assert data["total_platform_fees_cents"] == p.platform_fee_cents
    assert data["total_professional_amount_cents"] == p.professional_amount_cents
    assert data["total_refunded_cents"] == 0


@pytest.mark.asyncio
async def test_admin_summary_endpoint_403_non_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Non-admin users must receive 403."""
    token, _ = await _register_and_login(client, "patient.403sum@test.com", "patient")
    resp = await client.get(
        "/admin/financial/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_transactions_endpoint_ok(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _admin_token(client, db_session, "admin.txok@test.com")
    spec = await _seed_specialty(db_session, "ep-tx-ok")
    patient = await _seed_patient(db_session, "pat.eptxok@test.com")
    prof = await _seed_professional(db_session, "pro.eptxok@test.com", spec.id)

    for _ in range(3):
        cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)
        await _seed_payment(db_session, cr.id, patient.id, prof.id, payment_status=PaymentStatus.paid)

    resp = await client.get(
        "/admin/financial/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3


@pytest.mark.asyncio
async def test_admin_transactions_endpoint_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _admin_token(client, db_session, "admin.txpg@test.com")
    spec = await _seed_specialty(db_session, "ep-tx-page")
    patient = await _seed_patient(db_session, "pat.eptxpg@test.com")
    prof = await _seed_professional(db_session, "pro.eptxpg@test.com", spec.id)

    for _ in range(5):
        cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)
        await _seed_payment(db_session, cr.id, patient.id, prof.id)

    resp = await client.get(
        "/admin/financial/transactions?page=1&limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_admin_transactions_endpoint_filter_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _admin_token(client, db_session, "admin.txfst@test.com")
    spec = await _seed_specialty(db_session, "ep-tx-fst")
    patient = await _seed_patient(db_session, "pat.eptxfst@test.com")
    prof = await _seed_professional(db_session, "pro.eptxfst@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof.id)

    await _seed_payment(db_session, cr1.id, patient.id, prof.id, payment_status=PaymentStatus.paid)
    await _seed_payment(db_session, cr2.id, patient.id, prof.id, payment_status=PaymentStatus.pending)

    resp = await client.get(
        "/admin/financial/transactions?financial_status=paid",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["financial_status"] == "paid"


@pytest.mark.asyncio
async def test_admin_transactions_endpoint_filter_professional(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _admin_token(client, db_session, "admin.txfpf@test.com")
    spec = await _seed_specialty(db_session, "ep-tx-fpf")
    patient = await _seed_patient(db_session, "pat.eptxfpf@test.com")
    prof1 = await _seed_professional(db_session, "pro1.eptxfpf@test.com", spec.id)
    prof2 = await _seed_professional(db_session, "pro2.eptxfpf@test.com", spec.id)

    cr1 = await _seed_matched_request(db_session, patient.id, spec.id, prof1.id)
    cr2 = await _seed_matched_request(db_session, patient.id, spec.id, prof2.id)

    await _seed_payment(db_session, cr1.id, patient.id, prof1.id)
    await _seed_payment(db_session, cr2.id, patient.id, prof2.id)

    resp = await client.get(
        f"/admin/financial/transactions?professional_id={prof1.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["professional_user_id"] == str(prof1.id)


@pytest.mark.asyncio
async def test_admin_transactions_endpoint_403_non_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_and_login(client, "pro.403tx@test.com", "professional")
    resp = await client.get(
        "/admin/financial/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_payouts_endpoint_basic(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _admin_token(client, db_session, "admin.pyok@test.com")
    spec = await _seed_specialty(db_session, "ep-py-ok")
    patient = await _seed_patient(db_session, "pat.eppyok@test.com")
    prof = await _seed_professional(db_session, "pro.eppyok@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    resp = await client.post(
        "/admin/financial/payouts",
        json={"payment_ids": [str(p.id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["payouts_created"] == 1
    assert data["payments_included"] == 1
    assert data["already_paid"] == 0
    assert len(data["professional_summaries"]) == 1
    s = data["professional_summaries"][0]
    assert s["professional_user_id"] == str(prof.id)
    assert s["payment_count"] == 1


@pytest.mark.asyncio
async def test_admin_payouts_endpoint_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Calling payouts twice with the same payment does not duplicate the payout."""
    token = await _admin_token(client, db_session, "admin.pyidemp@test.com")
    spec = await _seed_specialty(db_session, "ep-py-idemp")
    patient = await _seed_patient(db_session, "pat.eppyidemp@test.com")
    prof = await _seed_professional(db_session, "pro.eppyidemp@test.com", spec.id)
    cr = await _seed_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    p = await _seed_payment(db_session, cr.id, patient.id, prof.id, 10000, PaymentStatus.paid)

    r1 = await client.post(
        "/admin/financial/payouts",
        json={"payment_ids": [str(p.id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 201
    payout_id_first = r1.json()["professional_summaries"][0]["payout_id"]

    r2 = await client.post(
        "/admin/financial/payouts",
        json={"payment_ids": [str(p.id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 201
    data2 = r2.json()
    assert data2["payouts_created"] == 0
    assert data2["already_paid"] == 1

    # Verify payout_id in transaction list still points to first payout
    r3 = await client.get(
        "/admin/financial/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    items = r3.json()["items"]
    assert items[0]["payout_id"] == payout_id_first


@pytest.mark.asyncio
async def test_admin_payouts_endpoint_403_non_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token, _ = await _register_and_login(client, "patient.403py@test.com", "patient")
    resp = await client.post(
        "/admin/financial/payouts",
        json={"payment_ids": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
