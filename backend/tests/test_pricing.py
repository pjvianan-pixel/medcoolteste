"""Tests for F2 part 3: dynamic pricing engine and fixed quotes."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole
from app.services.pricing import calculate_price

# ── helpers ───────────────────────────────────────────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": "pw", "role": role})
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _create_admin(db: AsyncSession, email: str = "admin@test.com") -> User:
    admin = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hash_password("adminpw"),
        role=UserRole.admin,
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin


async def _login_admin(client: AsyncClient, email: str = "admin@test.com") -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "adminpw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _seed_specialty(
    db: AsyncSession, slug: str = "clinico-geral", name: str = "Clínico Geral"
) -> Specialty:
    specialty = Specialty(id=uuid.uuid4(), slug=slug, name=name, active=True)
    db.add(specialty)
    await db.commit()
    await db.refresh(specialty)
    return specialty


async def _seed_pricing(
    db: AsyncSession,
    specialty_id: uuid.UUID,
    base: int = 14990,
    min_p: int = 9990,
    max_p: int = 24990,
) -> SpecialtyPricing:
    pricing = SpecialtyPricing(
        id=uuid.uuid4(),
        specialty_id=specialty_id,
        base_price_cents=base,
        min_price_cents=min_p,
        max_price_cents=max_p,
    )
    db.add(pricing)
    await db.commit()
    await db.refresh(pricing)
    return pricing


async def _seed_patient_profile(db: AsyncSession, user_id: uuid.UUID) -> PatientProfile:
    profile = PatientProfile(
        id=uuid.uuid4(),
        user_id=user_id,
        full_name="Patient Test",
        cpf="12345678901",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


async def _make_professional_online(
    db: AsyncSession,
    professional_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
) -> None:
    """Helper: create professional specialty link and mark them online."""
    ps = ProfessionalSpecialty(
        professional_user_id=professional_user_id,
        specialty_id=specialty_id,
    )
    db.add(ps)
    presence = ProfessionalPresence(
        professional_user_id=professional_user_id,
        is_online=True,
        last_seen_at=datetime.now(tz=UTC),
    )
    db.add(presence)
    await db.commit()


# ── Admin: GET /admin/specialties/{id}/pricing ────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_get_pricing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    resp = await client.get(
        f"/admin/specialties/{spec.id}/pricing",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["specialty_id"] == str(spec.id)
    assert data["base_price_cents"] == 14990
    assert data["min_price_cents"] == 9990
    assert data["max_price_cents"] == 24990


@pytest.mark.asyncio
async def test_admin_get_pricing_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)
    # No pricing seeded

    resp = await client.get(
        f"/admin/specialties/{spec.id}/pricing",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_get_pricing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "patient@test.com", "patient")

    resp = await client.get(
        f"/admin/specialties/{spec.id}/pricing",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── Admin: PUT /admin/specialties/{id}/pricing ────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_configure_pricing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)

    resp = await client.put(
        f"/admin/specialties/{spec.id}/pricing",
        json={"base_price_cents": 19990, "min_price_cents": 12990, "max_price_cents": 34990},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_price_cents"] == 19990
    assert data["min_price_cents"] == 12990
    assert data["max_price_cents"] == 34990


@pytest.mark.asyncio
async def test_admin_can_update_existing_pricing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    resp = await client.put(
        f"/admin/specialties/{spec.id}/pricing",
        json={"base_price_cents": 25000},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["base_price_cents"] == 25000
    # Other fields unchanged
    assert resp.json()["min_price_cents"] == 9990


@pytest.mark.asyncio
async def test_admin_put_pricing_missing_fields_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Creating new pricing requires all three price fields."""
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)

    resp = await client.put(
        f"/admin/specialties/{spec.id}/pricing",
        json={"base_price_cents": 19990},  # missing min and max
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_put_pricing_unknown_specialty_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    resp = await client.put(
        f"/admin/specialties/{uuid.uuid4()}/pricing",
        json={"base_price_cents": 19990, "min_price_cents": 12990, "max_price_cents": 34990},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── Pricing engine (unit-level via service) ───────────────────────────────────


@pytest.mark.asyncio
async def test_price_respects_min(db_session: AsyncSession) -> None:
    """Suggested price must never go below min_price_cents."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=14990, min_p=14000, max_p=24990)

    result = await calculate_price(spec.id, db_session, demand=0)
    assert result.suggested_price_cents >= 14000


@pytest.mark.asyncio
async def test_price_respects_max(db_session: AsyncSession) -> None:
    """Suggested price must never go above max_price_cents."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=24990, min_p=9990, max_p=24990)

    result = await calculate_price(spec.id, db_session, demand=100)
    assert result.suggested_price_cents <= 24990


@pytest.mark.asyncio
async def test_price_increases_when_demand_exceeds_supply(db_session: AsyncSession) -> None:
    """When demand > supply, the multiplier should push the price above base."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=14990, min_p=9990, max_p=24990)

    # supply=0 (no online professionals), demand=10
    result = await calculate_price(spec.id, db_session, demand=10)
    assert result.suggested_price_cents > result.base_price_cents
    assert result.multiplier > 1.0


@pytest.mark.asyncio
async def test_price_at_base_when_supply_equals_demand(db_session: AsyncSession) -> None:
    """When supply == demand the ratio is 1.0, so no markup is applied."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=14990, min_p=9990, max_p=24990)

    # demand=0, supply also effectively 0 → ratio = 1/1 = 1.0
    result = await calculate_price(spec.id, db_session, demand=0)
    assert result.multiplier == 1.0


@pytest.mark.asyncio
async def test_multiplier_clamped_at_1_5(db_session: AsyncSession) -> None:
    """Multiplier must never exceed 1.5 regardless of demand/supply ratio."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=10000, min_p=5000, max_p=99999)

    result = await calculate_price(spec.id, db_session, demand=1000)
    assert result.multiplier <= 1.5


@pytest.mark.asyncio
async def test_price_higher_with_online_supply(db_session: AsyncSession) -> None:
    """With professionals online (high supply), price should be at base (no markup)."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=14990, min_p=9990, max_p=24990)

    # Register a professional and put them online for this specialty
    pro_user = User(
        id=uuid.uuid4(),
        email="pro@pricing.test",
        hashed_password=hash_password("pw"),
        role=UserRole.professional,
        is_active=True,
    )
    db_session.add(pro_user)
    await db_session.commit()
    await _make_professional_online(db_session, pro_user.id, spec.id)

    # With supply=1, demand=0 → ratio < 1 → clamped to 1.0 (no markup)
    result = await calculate_price(spec.id, db_session, demand=0)
    assert result.multiplier == 1.0
    assert result.supply == 1


@pytest.mark.asyncio
async def test_pricing_not_configured_raises(db_session: AsyncSession) -> None:
    spec = await _seed_specialty(db_session, "noprice", "No Price")
    # No pricing record

    with pytest.raises(ValueError, match="No pricing configured"):
        await calculate_price(spec.id, db_session)


# ── Patient: POST /patients/me/quotes ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_patient_can_create_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    token = await _register_and_login(client, "patient@test.com", "patient")
    # Create patient profile
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    patient_id = me_resp.json()["id"]
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    resp = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["specialty_id"] == str(spec.id)
    assert data["patient_user_id"] == patient_id
    assert data["quoted_price_cents"] > 0
    assert data["currency"] == "BRL"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_quote_price_is_fixed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two quotes for the same specialty should return the same price (same conditions)."""
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id, base=14990, min_p=9990, max_p=24990)

    token = await _register_and_login(client, "patient@test.com", "patient")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    patient_id = me_resp.json()["id"]
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    resp1 = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp2 = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp1.status_code == 201
    assert resp2.status_code == 201
    # Different quote IDs
    assert resp1.json()["id"] != resp2.json()["id"]
    # Same price (same market conditions)
    assert resp1.json()["quoted_price_cents"] == resp2.json()["quoted_price_cents"]


@pytest.mark.asyncio
async def test_quote_has_expiry_in_future(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    token = await _register_and_login(client, "patient@test.com", "patient")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    patient_id = me_resp.json()["id"]
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    resp = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    expires_at = datetime.fromisoformat(data["expires_at"])
    created_at = datetime.fromisoformat(data["created_at"])
    # expires_at should be approximately 5 minutes after created_at
    diff = expires_at - created_at
    assert timedelta(minutes=4) < diff < timedelta(minutes=6)


@pytest.mark.asyncio
async def test_quote_inactive_specialty_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = Specialty(id=uuid.uuid4(), slug="inactive", name="Inactive", active=False)
    db_session.add(spec)
    await db_session.commit()

    token = await _register_and_login(client, "patient@test.com", "patient")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    patient_id = me_resp.json()["id"]
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    resp = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_quote_no_pricing_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    # No pricing record

    token = await _register_and_login(client, "patient@test.com", "patient")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    patient_id = me_resp.json()["id"]
    await _seed_patient_profile(db_session, uuid.UUID(patient_id))

    resp = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_professional_cannot_create_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    await _seed_pricing(db_session, spec.id)

    token = await _register_and_login(client, "pro@test.com", "professional")
    resp = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_create_quote(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    resp = await client.post(
        "/patients/me/quotes",
        json={"specialty_id": str(spec.id)},
    )
    assert resp.status_code == 401
