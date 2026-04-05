"""Tests for F4 Part 3: cancellation policy and no-show handling."""
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.consult_offer import ConsultOfferStatus
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentEvent, PaymentEventType, PaymentStatus
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole
from app.integrations.pagarme_client import (
    PaymentGatewayRefundResponse,
)
from app.services.cancellation import (
    CancellationPolicy,
    cancel_by_patient,
    cancel_by_professional,
    mark_no_show,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


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


async def _seed_patient(
    db: AsyncSession, email: str
) -> tuple[User, PatientProfile]:
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
    db: AsyncSession,
    email: str,
    specialty_id: uuid.UUID,
    approved: bool = True,
    online: bool = True,
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

    status_v = VerificationStatus.approved if approved else VerificationStatus.pending
    profile = ProfessionalProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name=f"Dr {email}",
        crm=f"CRM{email[:8]}",
        specialty="general",
        status_verificacao=status_v,
    )
    db.add(profile)

    ps = ProfessionalSpecialty(professional_user_id=user.id, specialty_id=specialty_id)
    db.add(ps)

    if online:
        presence = ProfessionalPresence(
            professional_user_id=user.id,
            is_online=True,
            last_seen_at=datetime.now(tz=UTC),
        )
        db.add(presence)

    await db.commit()
    await db.refresh(user)
    return user


async def _create_matched_request(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    price_cents: int = 10000,
    scheduled_at: datetime | None = None,
) -> tuple[ConsultRequest, ConsultQuote]:
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
        complaint="dor de cabeça",
        status=ConsultRequestStatus.matched,
        matched_professional_user_id=professional_user_id,
        scheduled_at=scheduled_at,
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    await db.refresh(quote)
    return cr, quote


async def _seed_payment(
    db: AsyncSession,
    consult_request: ConsultRequest,
    patient_id: uuid.UUID,
    professional_id: uuid.UUID,
    amount_cents: int = 10000,
    pay_status: PaymentStatus = PaymentStatus.paid,
    provider_charge_id: str | None = "ch_test123",
) -> Payment:
    payment = Payment(
        id=uuid.uuid4(),
        consult_request_id=consult_request.id,
        patient_user_id=patient_id,
        professional_user_id=professional_id,
        amount_cents=amount_cents,
        currency="BRL",
        platform_fee_cents=round(amount_cents * 0.2),
        professional_amount_cents=round(amount_cents * 0.8),
        provider="pagarme" if provider_charge_id else "pending",
        provider_payment_id="or_test123" if provider_charge_id else None,
        provider_charge_id=provider_charge_id,
        status=pay_status,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


def _make_refund_client(
    refund_id: str = "re_test001",
    amount_cents: int = 10000,
) -> MagicMock:
    """Build a mock gateway client that returns a successful refund."""
    client = MagicMock()
    client.create_refund = AsyncMock(
        return_value=PaymentGatewayRefundResponse(
            gateway_refund_id=refund_id,
            status="refunded",
            amount_cents=amount_cents,
        )
    )
    return client


# ── Service-level tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_by_patient_early_full_refund(db_session: AsyncSession) -> None:
    """Patient cancels > min_hours before consultation → full refund."""
    spec = await _seed_specialty(db_session, "cancel-early")
    patient, _ = await _seed_patient(db_session, "pat@cancel1.com")
    prof = await _seed_professional(db_session, "prof@cancel1.com", spec.id)

    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=48)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    policy = CancellationPolicy(
        min_hours_full_refund=24,
        late_cancellation_fee_percent=50,
        no_show_refund_percent=0,
    )
    mock_client = _make_refund_client("re_early", 10000)

    updated_cr = await cancel_by_patient(
        cr, db_session, gateway_client=mock_client, policy=policy
    )
    await db_session.commit()

    assert updated_cr.status == ConsultRequestStatus.cancelled_by_patient
    assert updated_cr.cancelled_at is not None

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.refund_pending

    mock_client.create_refund.assert_called_once_with(payment, amount=10000)

    # Verify PaymentEvent was created
    result = await db_session.execute(
        select(PaymentEvent).where(
            PaymentEvent.payment_id == payment.id,
            PaymentEvent.event_type == PaymentEventType.refund_requested,
        )
    )
    event = result.scalar_one()
    assert event.gateway_event_id == "re_early"


@pytest.mark.asyncio
async def test_cancel_by_patient_late_partial_refund(db_session: AsyncSession) -> None:
    """Patient cancels < min_hours before consultation → partial refund (50% retained)."""
    spec = await _seed_specialty(db_session, "cancel-late")
    patient, _ = await _seed_patient(db_session, "pat@cancel2.com")
    prof = await _seed_professional(db_session, "prof@cancel2.com", spec.id)

    # scheduled_at is only 2 hours away → within the 24-hour cutoff
    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=2)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    policy = CancellationPolicy(
        min_hours_full_refund=24,
        late_cancellation_fee_percent=50,
        no_show_refund_percent=0,
    )
    # retained = 50% of 10000 = 5000; refunded = 5000
    mock_client = _make_refund_client("re_late", 5000)

    await cancel_by_patient(
        cr, db_session, gateway_client=mock_client, policy=policy
    )
    await db_session.commit()

    assert cr.status == ConsultRequestStatus.cancelled_by_patient

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.refund_pending

    mock_client.create_refund.assert_called_once_with(payment, amount=5000)


@pytest.mark.asyncio
async def test_cancel_by_patient_no_refund_when_full_fee(db_session: AsyncSession) -> None:
    """100% late fee → no gateway refund call, payment event created."""
    spec = await _seed_specialty(db_session, "cancel-nofee")
    patient, _ = await _seed_patient(db_session, "pat@cancel3.com")
    prof = await _seed_professional(db_session, "prof@cancel3.com", spec.id)

    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=1)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    # 100% fee retained → 0% refunded
    policy = CancellationPolicy(
        min_hours_full_refund=24,
        late_cancellation_fee_percent=100,
        no_show_refund_percent=0,
    )
    mock_client = _make_refund_client()

    await cancel_by_patient(
        cr, db_session, gateway_client=mock_client, policy=policy
    )
    await db_session.commit()

    assert cr.status == ConsultRequestStatus.cancelled_by_patient
    mock_client.create_refund.assert_not_called()

    # Payment status should NOT be refund_pending (no refund issued)
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.paid


@pytest.mark.asyncio
async def test_cancel_by_patient_no_scheduled_at_full_refund(
    db_session: AsyncSession,
) -> None:
    """No scheduled_at set → defaults to full refund (patient-friendly)."""
    spec = await _seed_specialty(db_session, "cancel-noscheduled")
    patient, _ = await _seed_patient(db_session, "pat@cancel4.com")
    prof = await _seed_professional(db_session, "prof@cancel4.com", spec.id)

    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=None
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    policy = CancellationPolicy(min_hours_full_refund=24, late_cancellation_fee_percent=50)
    mock_client = _make_refund_client("re_noschedule", 10000)

    await cancel_by_patient(
        cr, db_session, gateway_client=mock_client, policy=policy
    )
    await db_session.commit()

    mock_client.create_refund.assert_called_once_with(payment, amount=10000)


@pytest.mark.asyncio
async def test_cancel_by_professional_always_full_refund(
    db_session: AsyncSession,
) -> None:
    """Professional cancels → patient always gets full refund regardless of timing."""
    spec = await _seed_specialty(db_session, "cancel-bypro")
    patient, _ = await _seed_patient(db_session, "pat@cancel5.com")
    prof = await _seed_professional(db_session, "prof@cancel5.com", spec.id)

    # 1 hour before → would be late for patient, but irrelevant for professional cancel
    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=1)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    mock_client = _make_refund_client("re_bypro", 10000)

    updated_cr = await cancel_by_professional(cr, db_session, gateway_client=mock_client)
    await db_session.commit()

    assert updated_cr.status == ConsultRequestStatus.cancelled_by_professional
    assert updated_cr.cancelled_at is not None

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.refund_pending

    mock_client.create_refund.assert_called_once_with(payment, amount=10000)


@pytest.mark.asyncio
async def test_cancel_by_professional_pending_payment_no_charge(
    db_session: AsyncSession,
) -> None:
    """Professional cancels a payment that was never captured → status=canceled, no gateway call."""
    spec = await _seed_specialty(db_session, "cancel-nopay")
    patient, _ = await _seed_patient(db_session, "pat@cancel6.com")
    prof = await _seed_professional(db_session, "prof@cancel6.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    payment = await _seed_payment(
        db_session, cr, patient.id, prof.id, 10000,
        pay_status=PaymentStatus.pending,
        provider_charge_id=None,
    )

    mock_client = _make_refund_client()

    await cancel_by_professional(cr, db_session, gateway_client=mock_client)
    await db_session.commit()

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.canceled
    mock_client.create_refund.assert_not_called()


@pytest.mark.asyncio
async def test_no_show_zero_refund(db_session: AsyncSession) -> None:
    """Patient no-show with 0% refund policy → payment stays PAID, no refund call."""
    spec = await _seed_specialty(db_session, "noshow-zero")
    patient, _ = await _seed_patient(db_session, "pat@noshow1.com")
    prof = await _seed_professional(db_session, "prof@noshow1.com", spec.id)

    # scheduled_at is 30 minutes in the past → past the grace period
    scheduled_at = datetime.now(tz=UTC) - timedelta(minutes=30)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    policy = CancellationPolicy(no_show_refund_percent=0, no_show_grace_minutes=15)
    mock_client = _make_refund_client()

    updated_cr = await mark_no_show(cr, db_session, gateway_client=mock_client, policy=policy)
    await db_session.commit()

    assert updated_cr.status == ConsultRequestStatus.no_show_patient
    assert updated_cr.no_show_marked_at is not None

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.paid
    mock_client.create_refund.assert_not_called()

    result = await db_session.execute(
        select(PaymentEvent).where(
            PaymentEvent.payment_id == payment.id,
            PaymentEvent.event_type == PaymentEventType.status_changed,
        )
    )
    event = result.scalar_one()
    payload = json.loads(event.raw_payload)
    assert payload["reason"] == "no_show_patient"
    assert payload["refund_amount_cents"] == 0


@pytest.mark.asyncio
async def test_no_show_partial_refund(db_session: AsyncSession) -> None:
    """Patient no-show with 30% refund policy → partial refund issued."""
    spec = await _seed_specialty(db_session, "noshow-partial")
    patient, _ = await _seed_patient(db_session, "pat@noshow2.com")
    prof = await _seed_professional(db_session, "prof@noshow2.com", spec.id)

    scheduled_at = datetime.now(tz=UTC) - timedelta(minutes=30)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    policy = CancellationPolicy(no_show_refund_percent=30, no_show_grace_minutes=15)
    mock_client = _make_refund_client("re_noshow", 3000)

    await mark_no_show(cr, db_session, gateway_client=mock_client, policy=policy)
    await db_session.commit()

    mock_client.create_refund.assert_called_once_with(payment, amount=3000)


@pytest.mark.asyncio
async def test_no_show_before_grace_period_raises(db_session: AsyncSession) -> None:
    """Cannot mark no-show before grace period has elapsed."""
    spec = await _seed_specialty(db_session, "noshow-grace")
    patient, _ = await _seed_patient(db_session, "pat@noshow3.com")
    prof = await _seed_professional(db_session, "prof@noshow3.com", spec.id)

    # scheduled_at is in the future → grace period not elapsed
    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=1)
    cr, _ = await _create_matched_request(
        db_session, patient.id, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )

    policy = CancellationPolicy(no_show_grace_minutes=15)
    with pytest.raises(ValueError, match="grace period"):
        await mark_no_show(cr, db_session, policy=policy)


@pytest.mark.asyncio
async def test_cancel_idempotency_no_duplicate_refund(db_session: AsyncSession) -> None:
    """Calling cancel_by_patient twice does not create a second refund."""
    spec = await _seed_specialty(db_session, "cancel-idem")
    patient, _ = await _seed_patient(db_session, "pat@idem1.com")
    prof = await _seed_professional(db_session, "prof@idem1.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    payment = await _seed_payment(db_session, cr, patient.id, prof.id, 10000)

    policy = CancellationPolicy(min_hours_full_refund=24, late_cancellation_fee_percent=50)
    mock_client = _make_refund_client("re_idem", 10000)

    await cancel_by_patient(cr, db_session, gateway_client=mock_client, policy=policy)
    await db_session.commit()

    # Attempt to cancel again (simulates duplicate request)
    with pytest.raises(ValueError):
        await cancel_by_patient(cr, db_session, gateway_client=mock_client, policy=policy)

    # create_refund should have been called only once
    assert mock_client.create_refund.call_count == 1


@pytest.mark.asyncio
async def test_cancel_invalid_status_raises(db_session: AsyncSession) -> None:
    """cancel_by_patient raises ValueError for non-matched requests."""
    spec = await _seed_specialty(db_session, "cancel-invalid")
    patient, _ = await _seed_patient(db_session, "pat@invalid1.com")
    prof = await _seed_professional(db_session, "prof@invalid1.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient.id, spec.id, prof.id, 10000)
    cr.status = ConsultRequestStatus.cancelled_by_patient
    await db_session.commit()

    with pytest.raises(ValueError, match="cannot be cancelled"):
        await cancel_by_patient(cr, db_session)


# ── API endpoint tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_cancel_unmatched_request_via_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient can cancel an unmatched (queued) consult request via API."""
    spec = await _seed_specialty(db_session, "api-cancel-un")
    await _seed_pricing(db_session, spec.id)

    token, patient_id = await _register_and_login(client, "pat@api1.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(PatientProfile(id=uuid.uuid4(), user_id=patient_uuid, full_name="P", cpf=cpf))

    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_uuid,
        specialty_id=spec.id,
        quoted_price_cents=10000,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
        status=QuoteStatus.used,
    )
    db_session.add(quote)
    await db_session.flush()

    cr = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient_uuid,
        specialty_id=spec.id,
        quote_id=quote.id,
        complaint="test",
        status=ConsultRequestStatus.queued,
    )
    db_session.add(cr)
    await db_session.commit()

    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "canceled"


@pytest.mark.asyncio
async def test_patient_cancel_matched_request_via_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient can cancel a matched consult request via API; gets full refund."""
    spec = await _seed_specialty(db_session, "api-cancel-matched")
    await _seed_pricing(db_session, spec.id)

    token, patient_id = await _register_and_login(client, "pat@api2.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(PatientProfile(id=uuid.uuid4(), user_id=patient_uuid, full_name="P", cpf=cpf))

    prof = await _seed_professional(db_session, "prof@api2.com", spec.id)

    # scheduled_at far in the future → full refund
    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=48)
    cr, _ = await _create_matched_request(
        db_session, patient_uuid, spec.id, prof.id, 10000, scheduled_at=scheduled_at
    )
    await _seed_payment(db_session, cr, patient_uuid, prof.id, 10000)

    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "cancelled_by_patient"


@pytest.mark.asyncio
async def test_patient_cannot_cancel_another_patients_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A patient cannot cancel another patient's consult request."""
    spec = await _seed_specialty(db_session, "api-cancel-other")
    await _seed_pricing(db_session, spec.id)

    token1, pid1 = await _register_and_login(client, "pat1@api3.com", "patient")
    token2, pid2 = await _register_and_login(client, "pat2@api3.com", "patient")
    p1_uuid = uuid.UUID(pid1)
    p2_uuid = uuid.UUID(pid2)

    for uid in [p1_uuid, p2_uuid]:
        cpf = str(uid.int)[:11].zfill(11)
        db_session.add(PatientProfile(id=uuid.uuid4(), user_id=uid, full_name="P", cpf=cpf))

    prof = await _seed_professional(db_session, "prof@api3.com", spec.id)
    cr, _ = await _create_matched_request(db_session, p1_uuid, spec.id, prof.id)
    await db_session.commit()

    # patient2 tries to cancel patient1's request
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/cancel",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_professional_cancel_via_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Professional can cancel a matched consult request via API."""
    spec = await _seed_specialty(db_session, "api-pro-cancel")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "pat@api4.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(PatientProfile(id=uuid.uuid4(), user_id=patient_uuid, full_name="P", cpf=cpf))

    prof_token, prof_id = await _register_and_login(client, "prof@api4.com", "professional")
    prof_uuid = uuid.UUID(prof_id)
    status_v = VerificationStatus.approved
    db_session.add(
        ProfessionalProfile(
            id=uuid.uuid4(),
            user_id=prof_uuid,
            full_name="Dr Prof",
            crm="CRM9999",
            specialty="general",
            status_verificacao=status_v,
        )
    )
    db_session.add(ProfessionalSpecialty(professional_user_id=prof_uuid, specialty_id=spec.id))
    await db_session.commit()

    cr, _ = await _create_matched_request(
        db_session, patient_uuid, spec.id, prof_uuid, 10000
    )
    await _seed_payment(db_session, cr, patient_uuid, prof_uuid, 10000)

    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/cancel",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "cancelled_by_professional"


@pytest.mark.asyncio
async def test_professional_no_show_via_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Professional can mark patient no-show via API."""
    spec = await _seed_specialty(db_session, "api-noshow")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "pat@api5.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(PatientProfile(id=uuid.uuid4(), user_id=patient_uuid, full_name="P", cpf=cpf))

    prof_token, prof_id = await _register_and_login(client, "prof@api5.com", "professional")
    prof_uuid = uuid.UUID(prof_id)
    db_session.add(
        ProfessionalProfile(
            id=uuid.uuid4(),
            user_id=prof_uuid,
            full_name="Dr Prof",
            crm="CRM8888",
            specialty="general",
            status_verificacao=VerificationStatus.approved,
        )
    )
    db_session.add(ProfessionalSpecialty(professional_user_id=prof_uuid, specialty_id=spec.id))
    await db_session.commit()

    # scheduled_at in the past (> grace period)
    scheduled_at = datetime.now(tz=UTC) - timedelta(minutes=30)
    cr, _ = await _create_matched_request(
        db_session, patient_uuid, spec.id, prof_uuid, 10000, scheduled_at=scheduled_at
    )
    await _seed_payment(db_session, cr, patient_uuid, prof_uuid, 10000)

    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/no-show",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "no_show_patient"


@pytest.mark.asyncio
async def test_no_show_before_grace_period_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """API returns 422 when no-show is attempted before grace period."""
    spec = await _seed_specialty(db_session, "api-noshow-early")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "pat@api6.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(PatientProfile(id=uuid.uuid4(), user_id=patient_uuid, full_name="P", cpf=cpf))

    prof_token, prof_id = await _register_and_login(client, "prof@api6.com", "professional")
    prof_uuid = uuid.UUID(prof_id)
    db_session.add(
        ProfessionalProfile(
            id=uuid.uuid4(),
            user_id=prof_uuid,
            full_name="Dr Prof",
            crm="CRM7777",
            specialty="general",
            status_verificacao=VerificationStatus.approved,
        )
    )
    db_session.add(ProfessionalSpecialty(professional_user_id=prof_uuid, specialty_id=spec.id))
    await db_session.commit()

    # scheduled_at in the future
    scheduled_at = datetime.now(tz=UTC) + timedelta(hours=1)
    cr, _ = await _create_matched_request(
        db_session, patient_uuid, spec.id, prof_uuid, 10000, scheduled_at=scheduled_at
    )

    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/no-show",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_double_cancel_idempotency_via_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Second cancel call returns 422 (already in terminal state)."""
    spec = await _seed_specialty(db_session, "api-double-cancel")
    await _seed_pricing(db_session, spec.id)

    token, patient_id = await _register_and_login(client, "pat@api7.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    cpf = str(patient_uuid.int)[:11].zfill(11)
    db_session.add(PatientProfile(id=uuid.uuid4(), user_id=patient_uuid, full_name="P", cpf=cpf))

    prof = await _seed_professional(db_session, "prof@api7.com", spec.id)
    cr, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof.id)
    await _seed_payment(db_session, cr, patient_uuid, prof.id)
    await db_session.commit()

    # First cancel
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    # Second cancel should fail
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
