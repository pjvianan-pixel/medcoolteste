"""Tests for F2 part 1: specialties catalog and professional-specialty links."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole

# ── helpers ──────────────────────────────────────────────────────────────────


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


# ── GET /specialties ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticated_user_can_list_specialties(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_specialty(db_session, "clinico-geral", "Clínico Geral")
    await _seed_specialty(db_session, "pediatria", "Pediatria")

    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get("/specialties", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    slugs = [s["slug"] for s in resp.json()]
    assert "clinico-geral" in slugs
    assert "pediatria" in slugs


@pytest.mark.asyncio
async def test_inactive_specialties_not_listed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    active = await _seed_specialty(db_session, "clinico-geral", "Clínico Geral")
    inactive = Specialty(id=uuid.uuid4(), slug="inactive-spec", name="Inactive", active=False)
    db_session.add(inactive)
    await db_session.commit()

    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get("/specialties", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    slugs = [s["slug"] for s in resp.json()]
    assert active.slug in slugs
    assert "inactive-spec" not in slugs


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_specialties(client: AsyncClient) -> None:
    resp = await client.get("/specialties")
    assert resp.status_code == 401


# ── GET /professionals/me/specialties ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_list_own_specialties(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")

    # Assign specialty via PUT first
    resp = await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    resp = await client.get(
        "/professionals/me/specialties",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["slug"] == spec.slug


@pytest.mark.asyncio
async def test_patient_cannot_list_professional_specialties(client: AsyncClient) -> None:
    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get(
        "/professionals/me/specialties",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── PUT /professionals/me/specialties ────────────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_set_specialties_by_slug(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")

    resp = await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["slug"] == spec.slug


@pytest.mark.asyncio
async def test_professional_can_set_specialties_by_uuid(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")

    resp = await client.put(
        "/professionals/me/specialties",
        json={"specialties": [str(spec.id)]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == str(spec.id)


@pytest.mark.asyncio
async def test_put_specialties_replaces_existing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec1 = await _seed_specialty(db_session, "clinico-geral", "Clínico Geral")
    spec2 = await _seed_specialty(db_session, "pediatria", "Pediatria")
    token = await _register_and_login(client, "pro@test.com", "professional")

    # Set spec1 first
    await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec1.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Replace with spec2 only
    resp = await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec2.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    slugs = [s["slug"] for s in resp.json()]
    assert spec1.slug not in slugs
    assert spec2.slug in slugs


@pytest.mark.asyncio
async def test_put_invalid_specialty_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")

    resp = await client.put(
        "/professionals/me/specialties",
        json={"specialties": ["non-existent-slug"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patient_cannot_set_specialties(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "patient@test.com", "patient")

    resp = await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── POST /admin/specialties ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_create_specialty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        "/admin/specialties",
        json={"slug": "cardiologia", "name": "Cardiologia"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["slug"] == "cardiologia"
    assert data["name"] == "Cardiologia"
    assert data["active"] is True


@pytest.mark.asyncio
async def test_admin_create_specialty_duplicate_slug_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    await _seed_specialty(db_session, "cardiologia", "Cardiologia")

    resp = await client.post(
        "/admin/specialties",
        json={"slug": "cardiologia", "name": "Cardiologia Duplicada"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_non_admin_cannot_create_specialty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    resp = await client.post(
        "/admin/specialties",
        json={"slug": "cardiologia", "name": "Cardiologia"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


# ── PATCH /admin/specialties/{id} ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_deactivate_specialty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)

    resp = await client.patch(
        f"/admin/specialties/{spec.id}",
        json={"active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is False


@pytest.mark.asyncio
async def test_admin_can_rename_specialty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)
    spec = await _seed_specialty(db_session)

    resp = await client.patch(
        f"/admin/specialties/{spec.id}",
        json={"name": "Clínica Geral"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Clínica Geral"


@pytest.mark.asyncio
async def test_admin_patch_nonexistent_specialty_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    resp = await client.patch(
        f"/admin/specialties/{uuid.uuid4()}",
        json={"active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_update_specialty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")

    resp = await client.patch(
        f"/admin/specialties/{spec.id}",
        json={"active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
