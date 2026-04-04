"""Tests for F2 part 2: professional presence (online/offline) and availability."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole

# ── helpers ──────────────────────────────────────────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": "pw", "role": role})
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _seed_specialty(
    db: AsyncSession, slug: str = "clinico-geral", name: str = "Clínico Geral"
) -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=name, active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


async def _seed_presence(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    is_online: bool = True,
    last_seen_offset_seconds: int = 0,
) -> ProfessionalPresence:
    """Seed a ProfessionalPresence row with a controllable last_seen_at."""
    last_seen = datetime.now(tz=UTC) - timedelta(seconds=last_seen_offset_seconds)
    presence = ProfessionalPresence(
        professional_user_id=user_id,
        is_online=is_online,
        last_seen_at=last_seen,
    )
    db.add(presence)
    await db.commit()
    await db.refresh(presence)
    return presence


async def _get_user_id(db: AsyncSession, email: str) -> uuid.UUID:
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    return user.id


# ── POST /professionals/me/online ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_go_online(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    resp = await client.post(
        "/professionals/me/online", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_online"] is True
    assert "last_seen_at" in data


@pytest.mark.asyncio
async def test_online_creates_presence_if_not_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro2@test.com", "professional")
    resp = await client.post(
        "/professionals/me/online", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_online"] is True


@pytest.mark.asyncio
async def test_patient_cannot_go_online(client: AsyncClient) -> None:
    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.post(
        "/professionals/me/online", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_go_online(client: AsyncClient) -> None:
    resp = await client.post("/professionals/me/online")
    assert resp.status_code == 401


# ── POST /professionals/me/offline ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_professional_can_go_offline(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    # Go online first
    await client.post(
        "/professionals/me/online", headers={"Authorization": f"Bearer {token}"}
    )
    resp = await client.post(
        "/professionals/me/offline", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_online"] is False


@pytest.mark.asyncio
async def test_offline_without_prior_online(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    resp = await client.post(
        "/professionals/me/offline", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_online"] is False


@pytest.mark.asyncio
async def test_patient_cannot_go_offline(client: AsyncClient) -> None:
    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.post(
        "/professionals/me/offline", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


# ── POST /professionals/me/heartbeat ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_keeps_professional_online(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    await client.post(
        "/professionals/me/online", headers={"Authorization": f"Bearer {token}"}
    )
    resp = await client.post(
        "/professionals/me/heartbeat", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_online"] is True


@pytest.mark.asyncio
async def test_heartbeat_updates_last_seen_at(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    r1 = await client.post(
        "/professionals/me/online", headers={"Authorization": f"Bearer {token}"}
    )
    ts1 = r1.json()["last_seen_at"]
    r2 = await client.post(
        "/professionals/me/heartbeat", headers={"Authorization": f"Bearer {token}"}
    )
    ts2 = r2.json()["last_seen_at"]
    # last_seen_at must not go backwards
    assert ts2 >= ts1


@pytest.mark.asyncio
async def test_heartbeat_creates_presence_if_not_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, "pro@test.com", "professional")
    resp = await client.post(
        "/professionals/me/heartbeat", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["is_online"] is True


@pytest.mark.asyncio
async def test_patient_cannot_send_heartbeat(client: AsyncClient) -> None:
    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.post(
        "/professionals/me/heartbeat", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


# ── Timeout / expiry logic ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timed_out_professional_not_counted_as_online(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A presence row older than PRESENCE_TIMEOUT_SECONDS should not be counted."""
    from app.core.config import settings

    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")
    pro_id = await _get_user_id(db_session, "pro@test.com")

    # Assign specialty
    await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Seed presence that has already timed out
    await _seed_presence(
        db_session,
        pro_id,
        is_online=True,
        last_seen_offset_seconds=settings.PRESENCE_TIMEOUT_SECONDS + 10,
    )

    any_token = token
    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {any_token}"}
    )
    assert resp.status_code == 200
    items = {item["slug"]: item for item in resp.json()["items"]}
    assert items[spec.slug]["online_count"] == 0


@pytest.mark.asyncio
async def test_active_presence_counted_as_online(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A fresh presence row should be counted as online."""
    from app.core.config import settings

    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")
    pro_id = await _get_user_id(db_session, "pro@test.com")

    # Assign specialty
    await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Seed fresh presence (within timeout)
    await _seed_presence(
        db_session,
        pro_id,
        is_online=True,
        last_seen_offset_seconds=max(0, settings.PRESENCE_TIMEOUT_SECONDS - 10),
    )

    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    items = {item["slug"]: item for item in resp.json()["items"]}
    assert items[spec.slug]["online_count"] == 1


# ── GET /specialties/availability ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_availability_returns_all_active_specialties(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_specialty(db_session, "clinico-geral", "Clínico Geral")
    await _seed_specialty(db_session, "pediatria", "Pediatria")

    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    slugs = [item["slug"] for item in resp.json()["items"]]
    assert "clinico-geral" in slugs
    assert "pediatria" in slugs


@pytest.mark.asyncio
async def test_availability_inactive_specialty_excluded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_specialty(db_session, "clinico-geral", "Clínico Geral")
    inactive = Specialty(id=uuid.uuid4(), slug="inactive", name="Inactive", active=False)
    db_session.add(inactive)
    await db_session.commit()

    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    slugs = [item["slug"] for item in resp.json()["items"]]
    assert "inactive" not in slugs


@pytest.mark.asyncio
async def test_availability_zero_count_when_no_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_specialty(db_session, "clinico-geral", "Clínico Geral")

    token = await _register_and_login(client, "patient@test.com", "patient")
    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    items = {item["slug"]: item for item in resp.json()["items"]}
    assert items["clinico-geral"]["online_count"] == 0


@pytest.mark.asyncio
async def test_availability_counts_multiple_professionals(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two online professionals in the same specialty should count as 2."""
    from app.core.config import settings

    spec = await _seed_specialty(db_session)

    token1 = await _register_and_login(client, "pro1@test.com", "professional")
    token2 = await _register_and_login(client, "pro2@test.com", "professional")
    pro1_id = await _get_user_id(db_session, "pro1@test.com")
    pro2_id = await _get_user_id(db_session, "pro2@test.com")

    for tok in (token1, token2):
        await client.put(
            "/professionals/me/specialties",
            json={"specialties": [spec.slug]},
            headers={"Authorization": f"Bearer {tok}"},
        )

    offset = max(0, settings.PRESENCE_TIMEOUT_SECONDS - 10)
    await _seed_presence(db_session, pro1_id, is_online=True, last_seen_offset_seconds=offset)
    await _seed_presence(db_session, pro2_id, is_online=True, last_seen_offset_seconds=offset)

    any_token = token1
    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {any_token}"}
    )
    assert resp.status_code == 200
    items = {item["slug"]: item for item in resp.json()["items"]}
    assert items[spec.slug]["online_count"] == 2


@pytest.mark.asyncio
async def test_availability_offline_professional_not_counted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    spec = await _seed_specialty(db_session)
    token = await _register_and_login(client, "pro@test.com", "professional")
    pro_id = await _get_user_id(db_session, "pro@test.com")

    await client.put(
        "/professionals/me/specialties",
        json={"specialties": [spec.slug]},
        headers={"Authorization": f"Bearer {token}"},
    )
    await _seed_presence(db_session, pro_id, is_online=False, last_seen_offset_seconds=0)

    resp = await client.get(
        "/specialties/availability", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    items = {item["slug"]: item for item in resp.json()["items"]}
    assert items[spec.slug]["online_count"] == 0


@pytest.mark.asyncio
async def test_availability_unauthenticated_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/specialties/availability")
    assert resp.status_code == 401
