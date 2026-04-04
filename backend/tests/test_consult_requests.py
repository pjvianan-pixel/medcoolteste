"""Tests for F2 part 4: consult requests and matching basics."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.consult_offer import ConsultOffer, ConsultOfferStatus
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole
from app.services.pricing import get_demand_for_specialty

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
    # Use first 11 digits of user_id hex as unique CPF
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


# ── Patient creates consult request ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_can_create_consult_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Tenho dor de cabeça"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["patient_user_id"] == patient_id
    assert data["specialty_id"] == str(spec.id)
    assert data["complaint"] == "Tenho dor de cabeça"
    assert data["status"] in ("queued", "offering")


@pytest.mark.asyncio
async def test_create_consult_request_marks_quote_used(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient2@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Febre alta"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    # Quote should now be 'used'
    await db_session.refresh(quote)
    assert quote.status == QuoteStatus.used


@pytest.mark.asyncio
async def test_cannot_use_expired_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient3@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    expired_quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=uuid.UUID(patient_id),
        specialty_id=spec.id,
        quoted_price_cents=14990,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) - timedelta(minutes=1),  # already expired
        status=QuoteStatus.active,
    )
    db_session.add(expired_quote)
    await db_session.commit()

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(expired_quote.id), "complaint": "Dor"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_cannot_use_another_patients_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token1, pid1 = await _register_and_login(client, "patientA@cr.com", "patient")
    _token2, pid2 = await _register_and_login(client, "patientB@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(pid1))
    await _seed_patient_profile(db_session, uuid.UUID(pid2))

    # Quote owned by patient B
    quote_b = await _create_active_quote(db_session, uuid.UUID(pid2), spec.id)

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote_b.id), "complaint": "Dor"},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_cannot_use_already_used_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient5@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    used_quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=uuid.UUID(patient_id),
        specialty_id=spec.id,
        quoted_price_cents=14990,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.used,
    )
    db_session.add(used_quote)
    await db_session.commit()

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(used_quote.id), "complaint": "Dor"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


# ── Matching: creates offers for approved and online professionals ─────────────


@pytest.mark.asyncio
async def test_matching_creates_offers_for_approved_online_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient6@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    # Create 2 approved+online professionals
    pro1 = await _seed_professional(db_session, "pro1@match.com", spec.id, approved=True, online=True)
    pro2 = await _seed_professional(db_session, "pro2@match.com", spec.id, approved=True, online=True)

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Tosse"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "offering"
    assert len(data["offers"]) == 2
    offer_pros = {o["professional_user_id"] for o in data["offers"]}
    assert str(pro1.id) in offer_pros
    assert str(pro2.id) in offer_pros


@pytest.mark.asyncio
async def test_matching_skips_non_approved_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient7@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    # Non-approved professional (pending)
    await _seed_professional(db_session, "pending@match.com", spec.id, approved=False, online=True)

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Tontura"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    # No approved+online professional → stays queued, no offers
    assert data["status"] == "queued"
    assert len(data["offers"]) == 0


@pytest.mark.asyncio
async def test_matching_skips_offline_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient8@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    # Approved but offline professional
    await _seed_professional(db_session, "offline@match.com", spec.id, approved=True, online=False)

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)

    resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Febre"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "queued"
    assert len(data["offers"]) == 0


# ── Professional accepts offer ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_accept_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    patient_token, patient_id = await _register_and_login(client, "patient9@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    pro = await _seed_professional(db_session, "pro3@match.com", spec.id, approved=True, online=True)
    # Login as professional
    pro_resp = await client.post("/auth/login", json={"email": "pro3@match.com", "password": "pw"})
    pro_token = pro_resp.json()["access_token"]

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)
    create_resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Dor nas costas"},
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert create_resp.status_code == 201
    cr_data = create_resp.json()
    assert cr_data["status"] == "offering"
    offer_id = cr_data["offers"][0]["id"]

    # Professional accepts
    accept_resp = await client.post(
        f"/professionals/me/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert accept_resp.status_code == 200, accept_resp.text
    assert accept_resp.json()["status"] == "accepted"

    # Consult request is now matched
    get_resp = await client.get(
        f"/patients/me/consult-requests/{cr_data['id']}",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "matched"
    assert get_resp.json()["matched_professional_user_id"] == str(pro.id)


@pytest.mark.asyncio
async def test_other_offers_expire_when_one_is_accepted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    patient_token, patient_id = await _register_and_login(client, "patient10@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    await _seed_professional(db_session, "pro4@match.com", spec.id, approved=True, online=True)
    await _seed_professional(db_session, "pro5@match.com", spec.id, approved=True, online=True)

    pro_resp1 = await client.post("/auth/login", json={"email": "pro4@match.com", "password": "pw"})
    pro_token1 = pro_resp1.json()["access_token"]

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)
    create_resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Cansaço"},
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert create_resp.status_code == 201
    cr_data = create_resp.json()
    assert len(cr_data["offers"]) == 2

    # Get the offer for pro4 (token1)
    offers_resp = await client.get(
        "/professionals/me/offers",
        headers={"Authorization": f"Bearer {pro_token1}"},
    )
    assert offers_resp.status_code == 200
    pro1_offers = offers_resp.json()
    assert len(pro1_offers) == 1
    offer_id = pro1_offers[0]["id"]

    # Pro4 accepts
    await client.post(
        f"/professionals/me/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {pro_token1}"},
    )

    # Pro5's offer should be expired
    pro_resp2 = await client.post("/auth/login", json={"email": "pro5@match.com", "password": "pw"})
    pro_token2 = pro_resp2.json()["access_token"]
    offers_resp2 = await client.get(
        "/professionals/me/offers",
        headers={"Authorization": f"Bearer {pro_token2}"},
    )
    assert offers_resp2.status_code == 200
    # No pending offers for pro5 (expired after pro4 accepted)
    assert len(offers_resp2.json()) == 0


# ── Patient cancels consult request ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_can_cancel_unmatched_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "patient11@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)
    create_resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Dor"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201
    cr_id = create_resp.json()["id"]

    cancel_resp = await client.post(
        f"/patients/me/consult-requests/{cr_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "canceled"


@pytest.mark.asyncio
async def test_patient_cannot_cancel_matched_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    patient_token, patient_id = await _register_and_login(client, "patient12@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    await _seed_professional(db_session, "pro6@match.com", spec.id, approved=True, online=True)
    pro_resp = await client.post("/auth/login", json={"email": "pro6@match.com", "password": "pw"})
    pro_token = pro_resp.json()["access_token"]

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)
    create_resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Náusea"},
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    cr_id = create_resp.json()["id"]
    offer_id = create_resp.json()["offers"][0]["id"]

    # Professional accepts
    await client.post(
        f"/professionals/me/offers/{offer_id}/accept",
        headers={"Authorization": f"Bearer {pro_token}"},
    )

    # Patient tries to cancel after match
    cancel_resp = await client.post(
        f"/patients/me/consult-requests/{cr_id}/cancel",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert cancel_resp.status_code == 422, cancel_resp.text


# ── Professional rejects offer ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_reject_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    patient_token, patient_id = await _register_and_login(client, "patient13@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    await _seed_professional(db_session, "pro7@match.com", spec.id, approved=True, online=True)
    pro_resp = await client.post("/auth/login", json={"email": "pro7@match.com", "password": "pw"})
    pro_token = pro_resp.json()["access_token"]

    quote = await _create_active_quote(db_session, uuid.UUID(patient_id), spec.id)
    create_resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Dor"},
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    offer_id = create_resp.json()["offers"][0]["id"]

    reject_resp = await client.post(
        f"/professionals/me/offers/{offer_id}/reject",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"


# ── Demand influences price ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_demand_from_queued_requests_influences_price(
    db_session: AsyncSession,
) -> None:
    """Active consult_requests (queued/offering) count as demand for pricing."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    # Initially no demand
    demand_0 = await get_demand_for_specialty(spec.id, db_session)
    assert demand_0 == 0

    # Insert a queued consult_request (requires a patient user and a quote)
    patient = User(
        id=uuid.uuid4(),
        email="demandtest@example.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(patient)
    await db_session.flush()

    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient.id,
        specialty_id=spec.id,
        quoted_price_cents=14990,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.used,
    )
    db_session.add(quote)
    await db_session.flush()

    cr = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient.id,
        specialty_id=spec.id,
        quote_id=quote.id,
        complaint="Test demand",
        status=ConsultRequestStatus.queued,
    )
    db_session.add(cr)
    await db_session.commit()

    demand_1 = await get_demand_for_specialty(spec.id, db_session)
    assert demand_1 == 1

    # A canceled request should not count
    cr2 = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient.id,
        specialty_id=spec.id,
        quote_id=uuid.uuid4(),  # fake, just for the test
        complaint="Test demand 2",
        status=ConsultRequestStatus.canceled,
    )
    db_session.add(cr2)
    await db_session.commit()

    demand_still_1 = await get_demand_for_specialty(spec.id, db_session)
    assert demand_still_1 == 1  # canceled doesn't count


@pytest.mark.asyncio
async def test_get_consult_request_returns_404_for_other_patient(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)
    token1, pid1 = await _register_and_login(client, "patientX@cr.com", "patient")
    token2, pid2 = await _register_and_login(client, "patientY@cr.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(pid1))
    await _seed_patient_profile(db_session, uuid.UUID(pid2))

    quote = await _create_active_quote(db_session, uuid.UUID(pid1), spec.id)
    create_resp = await client.post(
        "/patients/me/consult-requests",
        json={"quote_id": str(quote.id), "complaint": "Dor"},
        headers={"Authorization": f"Bearer {token1}"},
    )
    cr_id = create_resp.json()["id"]

    # Patient 2 tries to get patient 1's request
    resp = await client.get(
        f"/patients/me/consult-requests/{cr_id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 404
