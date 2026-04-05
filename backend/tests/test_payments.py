"""Tests for F4 Part 1: payments domain."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models.consult_offer import ConsultOffer, ConsultOfferStatus
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

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> tuple[str, str]:
    resp = await client.post("/auth/register", json={"email": email, "password": "pw", "role": role})
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
    await db.refresh(pricing)
    return pricing


async def _seed_patient_profile(db: AsyncSession, user_id: uuid.UUID) -> PatientProfile:
    cpf = str(user_id.int)[:11].zfill(11)
    profile = PatientProfile(
        id=uuid.uuid4(), user_id=user_id, full_name="Test Patient", cpf=cpf
    )
    db.add(profile)
    await db.commit()
    return profile


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


async def _create_active_quote(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
    price_cents: int = 14990,
) -> ConsultQuote:
    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_user_id,
        specialty_id=specialty_id,
        quoted_price_cents=price_cents,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.active,
    )
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return quote


async def _create_matched_request(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    price_cents: int = 14990,
) -> tuple[ConsultRequest, ConsultQuote]:
    """Seed a fully-matched consult request bypassing the API."""
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
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    await db.refresh(quote)
    return cr, quote


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_creates_payment_for_matched_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient can create a payment for a matched consult request."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient@pay1.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)
    prof = await _seed_professional(db_session, "prof@pay1.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof.id, 14990)

    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "pending"
    assert data["amount_cents"] == 14990
    assert data["consult_request_id"] == str(cr.id)
    assert data["patient_user_id"] == str(patient_uuid)
    assert data["professional_user_id"] == str(prof.id)
    # Verify split sums correctly
    assert data["platform_fee_cents"] + data["professional_amount_cents"] == 14990


@pytest.mark.asyncio
async def test_platform_fee_calculation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """platform_fee_cents and professional_amount_cents use PLATFORM_FEE_PERCENT."""
    spec = await _seed_specialty(db_session, "cardiologia")
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient@pay2.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)
    prof = await _seed_professional(db_session, "prof@pay2.com", spec.id)

    price = 10000
    cr, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof.id, price)

    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    expected_fee = round(price * settings.PLATFORM_FEE_PERCENT / 100)
    expected_professional = price - expected_fee

    assert data["platform_fee_cents"] == expected_fee
    assert data["professional_amount_cents"] == expected_professional


@pytest.mark.asyncio
async def test_cannot_create_payment_for_another_patients_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A patient cannot create a payment for another patient's consult request."""
    spec = await _seed_specialty(db_session, "neurologia")
    await _seed_pricing(db_session, spec.id)

    token1, patient_id1 = await _register_and_login(client, "patient@pay3a.com", "patient")
    token2, patient_id2 = await _register_and_login(client, "patient@pay3b.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id1))
    await _seed_patient_profile(db_session, uuid.UUID(patient_id2))
    prof = await _seed_professional(db_session, "prof@pay3.com", spec.id)

    # Create a matched request for patient1
    cr, _ = await _create_matched_request(
        db_session, uuid.UUID(patient_id1), spec.id, prof.id
    )

    # Patient2 tries to pay for patient1's request
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_cannot_create_duplicate_active_payment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cannot create a second active payment for the same consult request."""
    spec = await _seed_specialty(db_session, "ortopedia")
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient@pay4.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)
    prof = await _seed_professional(db_session, "prof@pay4.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof.id)

    # First payment succeeds
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    # Second attempt should fail
    resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert "active payment" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_create_payment_for_unmatched_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Creating a payment fails if the consult request is not yet matched."""
    spec = await _seed_specialty(db_session, "dermatologia")
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient@pay5.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)

    quote = await _create_active_quote(db_session, patient_uuid, spec.id)
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
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert "not matched" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patient_gets_payment_details(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient can retrieve their payment details by ID."""
    spec = await _seed_specialty(db_session, "pediatria")
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient@pay6.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)
    prof = await _seed_professional(db_session, "prof@pay6.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof.id)

    create_resp = await client.post(
        f"/patients/me/consult-requests/{cr.id}/payments",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    payment_id = create_resp.json()["id"]

    resp = await client.get(
        f"/patients/me/payments/{payment_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == payment_id
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_professional_lists_only_own_payments(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Professional sees only payments for their own matched consult requests."""
    spec = await _seed_specialty(db_session, "psiquiatria")
    await _seed_pricing(db_session, spec.id)

    token_pat, patient_id = await _register_and_login(client, "patient@pay7.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)

    prof1 = await _seed_professional(db_session, "prof1@pay7.com", spec.id)
    prof2 = await _seed_professional(db_session, "prof2@pay7.com", spec.id)

    # Create matched request for prof1 and prof2
    cr1, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof1.id, 10000)
    cr2, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof2.id, 20000)

    # Seed payments directly
    pay1 = Payment(
        id=uuid.uuid4(),
        consult_request_id=cr1.id,
        patient_user_id=patient_uuid,
        professional_user_id=prof1.id,
        amount_cents=10000,
        currency="BRL",
        platform_fee_cents=2000,
        professional_amount_cents=8000,
        provider="pending",
        status=PaymentStatus.pending,
    )
    pay2 = Payment(
        id=uuid.uuid4(),
        consult_request_id=cr2.id,
        patient_user_id=patient_uuid,
        professional_user_id=prof2.id,
        amount_cents=20000,
        currency="BRL",
        platform_fee_cents=4000,
        professional_amount_cents=16000,
        provider="pending",
        status=PaymentStatus.pending,
    )
    db_session.add(pay1)
    db_session.add(pay2)
    await db_session.commit()

    # Login as prof1 and check payments
    resp = await client.post("/auth/login", json={"email": "prof1@pay7.com", "password": "pw"})
    assert resp.status_code == 200, resp.text
    prof1_token = resp.json()["access_token"]

    resp = await client.get(
        "/professionals/me/payments",
        headers={"Authorization": f"Bearer {prof1_token}"},
    )
    assert resp.status_code == 200, resp.text
    payments = resp.json()
    assert len(payments) == 1
    assert payments[0]["id"] == str(pay1.id)
    assert payments[0]["professional_user_id"] == str(prof1.id)
