"""Tests for F2 part 5: counter offers + patient decision (with history)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.consult_offer import (
    ConsultOffer,
    ConsultOfferStatus,
    CounterStatus,
)
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> tuple[str, str]:
    resp = await client.post(
        "/auth/register", json={"email": email, "password": "pw", "role": role}
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"], user_id


async def _seed_specialty(db: AsyncSession, slug: str = "clinico-geral") -> Specialty:
    spec = Specialty(
        id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True
    )
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


async def _seed_offer(
    db: AsyncSession,
    consult_request_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    price_cents: int = 14990,
    offer_status: ConsultOfferStatus = ConsultOfferStatus.pending,
) -> ConsultOffer:
    offer = ConsultOffer(
        id=uuid.uuid4(),
        consult_request_id=consult_request_id,
        professional_user_id=professional_user_id,
        price_cents=price_cents,
        status=offer_status,
        sent_at=datetime.now(tz=UTC),
    )
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


async def _seed_consult_request(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
    req_status: ConsultRequestStatus = ConsultRequestStatus.offering,
) -> ConsultRequest:
    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_user_id,
        specialty_id=specialty_id,
        quoted_price_cents=14990,
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
        status=req_status,
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return cr


# ── Tests: professional creates counter offer ─────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_create_counter_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Professional sends a counter offer; event is recorded."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "co-patient@test.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    prof_token, prof_id = await _register_and_login(
        client, "co-prof@test.com", "professional"
    )
    await _seed_professional(db_session, "co-prof2@test.com", spec.id)

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    resp = await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["counter_status"] == "pending"
    assert data["counter_price_cents"] == 19990
    assert data["counter_proposed_at"] is not None
    assert len(data["events"]) == 1
    evt = data["events"][0]
    assert evt["actor_role"] == "professional"
    assert evt["event_type"] == "counter_proposed"
    assert evt["price_cents"] == 19990


@pytest.mark.asyncio
async def test_counter_offer_requires_pending_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cannot create counter offer on a non-pending offer."""
    spec = await _seed_specialty(db_session, "derm")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "co2-patient@test.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "co2-prof@test.com", "professional"
    )

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(
        db_session, cr.id, uuid.UUID(prof_id), offer_status=ConsultOfferStatus.rejected
    )

    resp = await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_counter_offer_requires_unmatched_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cannot create counter offer when request is already matched."""
    spec = await _seed_specialty(db_session, "cardio")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "co3-patient@test.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "co3-prof@test.com", "professional"
    )

    cr = await _seed_consult_request(
        db_session, uuid.UUID(patient_id), spec.id, ConsultRequestStatus.matched
    )
    offer = await _seed_offer(
        db_session, cr.id, uuid.UUID(prof_id), offer_status=ConsultOfferStatus.pending
    )

    resp = await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_professional_cannot_counter_other_professional_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Professional cannot counter-propose on another professional's offer (403/404)."""
    spec = await _seed_specialty(db_session, "ortho")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "co4-patient@test.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    prof_token, _prof_id = await _register_and_login(
        client, "co4-prof@test.com", "professional"
    )
    other_prof = await _seed_professional(db_session, "co4-other@test.com", spec.id)

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, other_prof.id)

    resp = await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 404


# ── Tests: patient accepts counter offer ──────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_accepts_counter_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient accepts counter → consult_request becomes matched, event recorded."""
    spec = await _seed_specialty(db_session, "neuro")
    await _seed_pricing(db_session, spec.id)

    patient_token, patient_id = await _register_and_login(
        client, "acc-patient@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "acc-prof@test.com", "professional"
    )

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    # Professional sends counter
    await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    # Patient accepts
    resp = await client.post(
        f"/patients/me/offers/{offer.id}/counter/accept",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["counter_status"] == "accepted"
    assert data["status"] == "accepted"
    events = data["events"]
    event_types = [e["event_type"] for e in events]
    assert "counter_proposed" in event_types
    assert "counter_accepted" in event_types

    # Check consult_request is matched
    resp2 = await client.get(
        f"/patients/me/consult-requests/{cr.id}",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert resp2.status_code == 200
    cr_data = resp2.json()
    assert cr_data["status"] == "matched"
    assert cr_data["matched_professional_user_id"] == prof_id


@pytest.mark.asyncio
async def test_patient_accept_expires_other_pending_offers(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """When patient accepts a counter, other pending offers expire."""
    spec = await _seed_specialty(db_session, "psy")
    await _seed_pricing(db_session, spec.id)

    patient_token, patient_id = await _register_and_login(
        client, "exp-patient@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "exp-prof@test.com", "professional"
    )
    other_prof = await _seed_professional(db_session, "exp-other@test.com", spec.id)

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))
    other_offer = await _seed_offer(db_session, cr.id, other_prof.id)

    # Professional sends counter
    await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    # Patient accepts
    await client.post(
        f"/patients/me/offers/{offer.id}/counter/accept",
        headers={"Authorization": f"Bearer {patient_token}"},
    )

    # The other offer should be expired
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.models.consult_offer import ConsultOffer  # noqa: PLC0415

    result = await db_session.execute(
        select(ConsultOffer).where(ConsultOffer.id == other_offer.id)
    )
    refreshed = result.scalar_one()
    assert refreshed.status == ConsultOfferStatus.expired


# ── Tests: patient rejects counter offer ──────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_rejects_counter_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient rejects counter → offer rejected, counter_status=rejected, event recorded."""
    spec = await _seed_specialty(db_session, "endo")
    await _seed_pricing(db_session, spec.id)

    patient_token, patient_id = await _register_and_login(
        client, "rej-patient@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "rej-prof@test.com", "professional"
    )

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    # Professional sends counter
    await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    # Patient rejects
    resp = await client.post(
        f"/patients/me/offers/{offer.id}/counter/reject",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["counter_status"] == "rejected"
    assert data["status"] == "rejected"
    event_types = [e["event_type"] for e in data["events"]]
    assert "counter_proposed" in event_types
    assert "counter_rejected" in event_types


@pytest.mark.asyncio
async def test_reject_reruns_matching_when_no_pending_offers(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """After rejecting counter, matching re-runs if no pending offers remain."""
    spec = await _seed_specialty(db_session, "urology")
    await _seed_pricing(db_session, spec.id)

    patient_token, patient_id = await _register_and_login(
        client, "rematch-patient@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "rematch-prof@test.com", "professional"
    )
    # A second eligible professional that will be matched after rejection
    await _seed_professional(db_session, "rematch-other@test.com", spec.id)

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    # Professional sends counter
    await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    # Patient rejects – no other pending offers remain, so re-matching should run
    await client.post(
        f"/patients/me/offers/{offer.id}/counter/reject",
        headers={"Authorization": f"Bearer {patient_token}"},
    )

    # Check that new offers were created (i.e., matching ran again)
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.models.consult_offer import ConsultOffer  # noqa: PLC0415

    result = await db_session.execute(
        select(ConsultOffer).where(
            ConsultOffer.consult_request_id == cr.id,
            ConsultOffer.status == ConsultOfferStatus.pending,
        )
    )
    new_offers = list(result.scalars().all())
    assert len(new_offers) >= 1, "Matching should have created new offers after counter rejection"


# ── Tests: authorization ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_cannot_accept_other_patients_offer(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient cannot accept/reject counter on another patient's offer (404)."""
    spec = await _seed_specialty(db_session, "ophth")
    await _seed_pricing(db_session, spec.id)

    _, patient_id = await _register_and_login(client, "auth-pat1@test.com", "patient")
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    other_patient_token, other_patient_id = await _register_and_login(
        client, "auth-pat2@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(other_patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "auth-prof@test.com", "professional"
    )

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    # Professional sends counter
    await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    # Other patient tries to accept – should fail with 404
    resp = await client.post(
        f"/patients/me/offers/{offer.id}/counter/accept",
        headers={"Authorization": f"Bearer {other_patient_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patient_cannot_accept_without_pending_counter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Patient cannot accept if counter_status is not pending."""
    spec = await _seed_specialty(db_session, "rheum")
    await _seed_pricing(db_session, spec.id)

    patient_token, patient_id = await _register_and_login(
        client, "noct-patient@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    _, prof_id = await _register_and_login(client, "noct-prof@test.com", "professional")

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    # No counter sent – counter_status is 'none'
    resp = await client.post(
        f"/patients/me/offers/{offer.id}/counter/accept",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_consult_request_includes_offer_events(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /patients/me/consult-requests/{id} includes events on offers."""
    spec = await _seed_specialty(db_session, "gast")
    await _seed_pricing(db_session, spec.id)

    patient_token, patient_id = await _register_and_login(
        client, "evt-patient@test.com", "patient"
    )
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))
    prof_token, prof_id = await _register_and_login(
        client, "evt-prof@test.com", "professional"
    )

    cr = await _seed_consult_request(db_session, uuid.UUID(patient_id), spec.id)
    offer = await _seed_offer(db_session, cr.id, uuid.UUID(prof_id))

    # Professional sends counter
    await client.post(
        f"/professionals/me/offers/{offer.id}/counter",
        json={"price_cents": 19990},
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    resp = await client.get(
        f"/patients/me/consult-requests/{cr.id}",
        headers={"Authorization": f"Bearer {patient_token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["offers"]) == 1
    offer_data = data["offers"][0]
    assert len(offer_data["events"]) == 1
    assert offer_data["events"][0]["event_type"] == "counter_proposed"
