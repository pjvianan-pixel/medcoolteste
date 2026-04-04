import pytest
from httpx import AsyncClient


# ── helpers ─────────────────────────────────────────────────────────────────

async def _register(client: AsyncClient, email: str, password: str, role: str) -> dict:
    resp = await client.post(
        "/auth/register", json={"email": email, "password": password, "role": role}
    )
    return resp


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── register ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_patient(client: AsyncClient) -> None:
    resp = await _register(client, "patient@test.com", "secret123", "patient")
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "patient@test.com"
    assert data["role"] == "patient"
    assert "id" in data


@pytest.mark.asyncio
async def test_register_professional(client: AsyncClient) -> None:
    resp = await _register(client, "pro@test.com", "secret123", "professional")
    assert resp.status_code == 201
    assert resp.json()["role"] == "professional"


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient) -> None:
    await _register(client, "dup@test.com", "pw", "patient")
    resp = await _register(client, "dup@test.com", "pw2", "patient")
    assert resp.status_code == 409


# ── login ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success(client: AsyncClient) -> None:
    await _register(client, "login@test.com", "mypassword", "patient")
    token = await _login(client, "login@test.com", "mypassword")
    assert isinstance(token, str) and len(token) > 0


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient) -> None:
    await _register(client, "wrong@test.com", "correct", "patient")
    resp = await client.post("/auth/login", json={"email": "wrong@test.com", "password": "bad"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/login", json={"email": "nobody@test.com", "password": "pw"}
    )
    assert resp.status_code == 401


# ── logout ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout(client: AsyncClient) -> None:
    resp = await client.post("/auth/logout")
    assert resp.status_code == 200
    assert "discard" in resp.json()["message"].lower()


# ── /me ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_authenticated(client: AsyncClient) -> None:
    await _register(client, "me@test.com", "pw", "patient")
    token = await _login(client, "me@test.com", "pw")
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@test.com"


@pytest.mark.asyncio
async def test_me_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_invalid_token(client: AsyncClient) -> None:
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer invalidtoken"})
    assert resp.status_code == 401


# ── role blocking ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_patient_cannot_access_professional_route(client: AsyncClient) -> None:
    await _register(client, "pat@test.com", "pw", "patient")
    token = await _login(client, "pat@test.com", "pw")
    resp = await client.get(
        "/professionals/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_professional_cannot_access_patient_route(client: AsyncClient) -> None:
    await _register(client, "prof@test.com", "pw", "professional")
    token = await _login(client, "prof@test.com", "pw")
    resp = await client.get(
        "/patients/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patient_can_access_own_route_no_profile(client: AsyncClient) -> None:
    await _register(client, "mypatient@test.com", "pw", "patient")
    token = await _login(client, "mypatient@test.com", "pw")
    resp = await client.get(
        "/patients/me", headers={"Authorization": f"Bearer {token}"}
    )
    # 404 because no profile was created, but 403 is NOT returned
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_professional_can_access_own_route_no_profile(client: AsyncClient) -> None:
    await _register(client, "mypro@test.com", "pw", "professional")
    token = await _login(client, "mypro@test.com", "pw")
    resp = await client.get(
        "/professionals/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
