"""Tests for admin professional verification endpoints."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.user import User, UserRole

# ── helpers ──────────────────────────────────────────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": "pw", "role": role})
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _create_admin(db: AsyncSession, email: str = "admin@test.com") -> User:
    import uuid

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


async def _create_professional_profile(
    db: AsyncSession,
    user_id: object,
    *,
    status: VerificationStatus = VerificationStatus.pending,
    crm: str = "CRM/SP 12345",
) -> ProfessionalProfile:
    import uuid

    profile = ProfessionalProfile(
        id=uuid.uuid4(),
        user_id=uuid.UUID(str(user_id)),
        full_name="Dr. Test",
        crm=crm,
        specialty="Cardiology",
        status_verificacao=status,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


# ── list pending ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_list_pending_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    pro_token = await _register_and_login(client, "pro1@test.com", "professional")
    # get pro user_id from /auth/me
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(db_session, pro_user_id)

    resp = await client.get(
        "/admin/professionals?status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status_verificacao"] == "pending"


@pytest.mark.asyncio
async def test_admin_list_all_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    # Create two professionals: one pending, one approved
    for i, ver_status in enumerate([VerificationStatus.pending, VerificationStatus.approved]):
        pro_token = await _register_and_login(client, f"pro{i}@test.com", "professional")
        me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
        pro_user_id = me_resp.json()["id"]
        await _create_professional_profile(
            db_session, pro_user_id, status=ver_status, crm=f"CRM/SP {i}"
        )

    resp = await client.get(
        "/admin/professionals",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_non_admin_cannot_list_professionals(client: AsyncClient) -> None:
    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get(
        "/admin/professionals", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_professional_cannot_access_admin_routes(client: AsyncClient) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    resp = await client.get(
        "/admin/professionals", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_professionals(client: AsyncClient) -> None:
    resp = await client.get("/admin/professionals")
    assert resp.status_code == 401


# ── approve ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_approve_professional(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    pro_token = await _register_and_login(client, "pro@test.com", "professional")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(db_session, pro_user_id)

    resp = await client.post(
        f"/admin/professionals/{pro_user_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status_verificacao"] == "approved"


@pytest.mark.asyncio
async def test_approve_clears_rejection_reason(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    pro_token = await _register_and_login(client, "pro@test.com", "professional")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(
        db_session, pro_user_id, status=VerificationStatus.rejected
    )

    resp = await client.post(
        f"/admin/professionals/{pro_user_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["verification_reason"] is None


@pytest.mark.asyncio
async def test_approve_non_existent_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    import uuid

    await _create_admin(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        f"/admin/professionals/{uuid.uuid4()}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_approve_patient_user_returns_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    patient_token = await _register_and_login(client, "patient@test.com", "patient")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {patient_token}"})
    patient_user_id = me_resp.json()["id"]

    resp = await client.post(
        f"/admin/professionals/{patient_user_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_cannot_approve(client: AsyncClient, db_session: AsyncSession) -> None:
    pro_token = await _register_and_login(client, "pro@test.com", "professional")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(db_session, pro_user_id)

    resp = await client.post(
        f"/admin/professionals/{pro_user_id}/approve",
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert resp.status_code == 403


# ── reject ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_reject_professional(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    pro_token = await _register_and_login(client, "pro@test.com", "professional")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(db_session, pro_user_id)

    resp = await client.post(
        f"/admin/professionals/{pro_user_id}/reject",
        json={"reason": "Documents are invalid"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status_verificacao"] == "rejected"
    assert data["verification_reason"] == "Documents are invalid"


@pytest.mark.asyncio
async def test_reject_saves_reason(client: AsyncClient, db_session: AsyncSession) -> None:
    await _create_admin(db_session)
    token = await _login_admin(client)

    pro_token = await _register_and_login(client, "pro@test.com", "professional")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(db_session, pro_user_id)

    reason = "CRM number not verifiable"
    resp = await client.post(
        f"/admin/professionals/{pro_user_id}/reject",
        json={"reason": reason},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["verification_reason"] == reason


@pytest.mark.asyncio
async def test_reject_non_existent_user_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    import uuid

    await _create_admin(db_session)
    token = await _login_admin(client)

    resp = await client.post(
        f"/admin/professionals/{uuid.uuid4()}/reject",
        json={"reason": "Not found"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_reject(client: AsyncClient, db_session: AsyncSession) -> None:
    pro_token = await _register_and_login(client, "pro@test.com", "professional")
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {pro_token}"})
    pro_user_id = me_resp.json()["id"]
    await _create_professional_profile(db_session, pro_user_id)

    resp = await client.post(
        f"/admin/professionals/{pro_user_id}/reject",
        json={"reason": "test"},
        headers={"Authorization": f"Bearer {pro_token}"},
    )
    assert resp.status_code == 403
