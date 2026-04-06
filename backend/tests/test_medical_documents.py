"""Tests for F5 Part 1 – Medical Documents (prescriptions and exam requests)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import DocumentSubtype, DocumentType, DocumentStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole

# ── Helpers ────────────────────────────────────────────────────────────────────


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


async def _seed_patient(db: AsyncSession, email: str) -> tuple[User, PatientProfile]:
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
        crm=f"CRM{str(user.id.int)[:6]}",
        specialty="general",
        status_verificacao=VerificationStatus.approved,
    )
    db.add(profile)
    ps = ProfessionalSpecialty(professional_user_id=user.id, specialty_id=specialty_id)
    db.add(ps)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_matched_consult(
    db: AsyncSession,
    patient_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_id: uuid.UUID,
    cr_status: ConsultRequestStatus = ConsultRequestStatus.matched,
) -> ConsultRequest:
    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_id,
        specialty_id=specialty_id,
        quoted_price_cents=10000,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.used,
    )
    db.add(quote)
    await db.flush()

    cr = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient_id,
        specialty_id=specialty_id,
        quote_id=quote.id,
        complaint="dor de cabeça",
        status=cr_status,
        matched_professional_user_id=professional_id,
        scheduled_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return cr


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def setup(db_session: AsyncSession, client: AsyncClient):
    """Seed a specialty, a patient, a professional, and a matched consult."""
    spec = await _seed_specialty(db_session)
    patient, _ = await _seed_patient(db_session, "patient@doc.com")
    professional = await _seed_professional(db_session, "doctor@doc.com", spec.id)
    # Login professional via the HTTP client so we get a real JWT
    resp = await client.post(
        "/auth/login", json={"email": "doctor@doc.com", "password": "pw"}
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    cr = await _create_matched_consult(db_session, patient.id, spec.id, professional.id)
    return {"token": token, "professional": professional, "patient": patient, "cr": cr}


# ── Tests: prescription ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_prescription_success(client: AsyncClient, setup):
    """Doctor can create a prescription with multiple medication items."""
    token = setup["token"]
    consult_id = setup["cr"].id

    payload = {
        "items": [
            {
                "drug_name": "Amoxicilina",
                "dosage": "500mg",
                "instructions": "1 cp 8/8h por 7 dias",
                "duration_days": 7,
            },
            {
                "drug_name": "Ibuprofeno",
                "dosage": "400mg",
                "instructions": "1 cp 12/12h se dor",
            },
        ]
    }

    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json=payload,
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["document_type"] == "PRESCRIPTION"
    assert data["status"] == "DRAFT"
    assert data["signature_type"] == "NONE"
    assert data["subtype"] is None
    assert len(data["content"]) == 2
    assert data["content"][0]["drug_name"] == "Amoxicilina"
    assert data["summary"] == "Amoxicilina"
    assert data["consult_request_id"] == str(consult_id)


@pytest.mark.asyncio
async def test_create_prescription_empty_items_rejected(client: AsyncClient, setup):
    """Prescription with empty items list is rejected with 422."""
    resp = await client.post(
        f"/professionals/me/consult-requests/{setup['cr'].id}/prescriptions",
        json={"items": []},
        headers=_auth(setup["token"]),
    )
    assert resp.status_code == 422


# ── Tests: exam request ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_exam_request_lab(client: AsyncClient, setup):
    """Doctor can create a lab exam request with correct subtype."""
    token = setup["token"]
    consult_id = setup["cr"].id

    payload = {
        "items": [
            {"exam_name": "Hemograma completo", "type": "LAB", "notes": "jejum 8h"},
            {"exam_name": "Glicemia", "type": "LAB"},
        ]
    }

    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/exam-requests",
        json=payload,
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["document_type"] == "EXAM_REQUEST"
    assert data["subtype"] == "LAB"
    assert data["summary"] == "Hemograma completo"
    assert len(data["content"]) == 2


@pytest.mark.asyncio
async def test_create_exam_request_imaging(client: AsyncClient, setup):
    """Doctor can create an imaging exam request with correct subtype."""
    payload = {
        "items": [
            {"exam_name": "Raio-X tórax", "type": "IMAGING", "notes": "AP e perfil"},
        ]
    }
    resp = await client.post(
        f"/professionals/me/consult-requests/{setup['cr'].id}/exam-requests",
        json=payload,
        headers=_auth(setup["token"]),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["subtype"] == "IMAGING"


@pytest.mark.asyncio
async def test_create_exam_request_mixed_subtype_is_none(client: AsyncClient, setup):
    """Mixed LAB+IMAGING exam request results in null subtype."""
    payload = {
        "items": [
            {"exam_name": "Hemograma", "type": "LAB"},
            {"exam_name": "Raio-X", "type": "IMAGING"},
        ]
    }
    resp = await client.post(
        f"/professionals/me/consult-requests/{setup['cr'].id}/exam-requests",
        json=payload,
        headers=_auth(setup["token"]),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["subtype"] is None


# ── Tests: list documents ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_documents_returns_all_types(client: AsyncClient, setup):
    """GET documents returns prescriptions and exam requests together."""
    token = setup["token"]
    consult_id = setup["cr"].id
    base = f"/professionals/me/consult-requests/{consult_id}"

    # Create one prescription and one exam request
    await client.post(
        f"{base}/prescriptions",
        json={"items": [{"drug_name": "Dipirona", "dosage": "500mg", "instructions": "se dor"}]},
        headers=_auth(token),
    )
    await client.post(
        f"{base}/exam-requests",
        json={"items": [{"exam_name": "TSH", "type": "LAB"}]},
        headers=_auth(token),
    )

    resp = await client.get(f"{base}/documents", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2
    types = {d["document_type"] for d in data}
    assert types == {"PRESCRIPTION", "EXAM_REQUEST"}


@pytest.mark.asyncio
async def test_list_documents_empty_for_new_consult(client: AsyncClient, setup):
    """GET documents returns an empty list when no documents have been created."""
    resp = await client.get(
        f"/professionals/me/consult-requests/{setup['cr'].id}/documents",
        headers=_auth(setup["token"]),
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ── Tests: authorization ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_document_for_nonexistent_consult_returns_404(
    client: AsyncClient, setup
):
    """Creating a document for a non-existent consult returns 404."""
    fake_id = uuid.uuid4()
    resp = await client.post(
        f"/professionals/me/consult-requests/{fake_id}/prescriptions",
        json={"items": [{"drug_name": "X", "dosage": "1mg", "instructions": "once"}]},
        headers=_auth(setup["token"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_document_for_other_professional_consult_returns_403(
    client: AsyncClient, db_session: AsyncSession, setup
):
    """A different professional cannot create documents for a consult they don't own."""
    spec = await _seed_specialty(db_session, slug="cardio-test")
    other_pro = await _seed_professional(db_session, "other@doc.com", spec.id)
    resp = await client.post(
        "/auth/login", json={"email": "other@doc.com", "password": "pw"}
    )
    other_token = resp.json()["access_token"]

    resp = await client.post(
        f"/professionals/me/consult-requests/{setup['cr'].id}/prescriptions",
        json={"items": [{"drug_name": "X", "dosage": "1mg", "instructions": "once"}]},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_documents_for_other_professional_consult_returns_403(
    client: AsyncClient, db_session: AsyncSession, setup
):
    """A different professional cannot list documents for a consult they don't own."""
    spec = await _seed_specialty(db_session, slug="neuro-test")
    other_pro = await _seed_professional(db_session, "neuro@doc.com", spec.id)
    resp = await client.post(
        "/auth/login", json={"email": "neuro@doc.com", "password": "pw"}
    )
    other_token = resp.json()["access_token"]

    resp = await client.get(
        f"/professionals/me/consult-requests/{setup['cr'].id}/documents",
        headers=_auth(other_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patient_cannot_access_documents_endpoint(
    client: AsyncClient, setup
):
    """Patients are not allowed to call professionals document endpoints (403)."""
    patient_token, _ = await _register_and_login(
        client, "patient2@doc.com", "patient"
    )
    resp = await client.get(
        f"/professionals/me/consult-requests/{setup['cr'].id}/documents",
        headers=_auth(patient_token),
    )
    # patient role → 403 from require_role guard
    assert resp.status_code == 403


# ── Tests: blocked statuses ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cannot_create_document_for_cancelled_consult(
    client: AsyncClient, db_session: AsyncSession
):
    """Document creation is blocked when the consult is cancelled_by_professional."""
    spec = await _seed_specialty(db_session, slug="blocked-test")
    patient, _ = await _seed_patient(db_session, "blocked_patient@doc.com")
    professional = await _seed_professional(db_session, "blocked_pro@doc.com", spec.id)
    resp = await client.post(
        "/auth/login", json={"email": "blocked_pro@doc.com", "password": "pw"}
    )
    token = resp.json()["access_token"]

    cr = await _create_matched_consult(
        db_session,
        patient.id,
        spec.id,
        professional.id,
        cr_status=ConsultRequestStatus.cancelled_by_professional,
    )

    resp = await client.post(
        f"/professionals/me/consult-requests/{cr.id}/prescriptions",
        json={"items": [{"drug_name": "Paracetamol", "dosage": "750mg", "instructions": "se dor"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 422


# ── Tests: F5 Part 2 – signing ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sign_prescription_success(client: AsyncClient, setup):
    """Professional can sign a DRAFT prescription; status becomes SIGNED."""
    token = setup["token"]
    consult_id = setup["cr"].id

    # Create a prescription first.
    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "Amoxicilina", "dosage": "500mg", "instructions": "8/8h"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]
    assert resp.json()["status"] == "DRAFT"

    # Sign the document.
    resp = await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "SIGNED"
    assert data["signature_type"] == "SIMPLE"
    assert data["signed_at"] is not None
    assert data["file_url"] is not None
    assert data["file_url"].startswith("/static/documents/")
    assert data["file_url"].endswith(".pdf")


@pytest.mark.asyncio
async def test_sign_exam_request_success(client: AsyncClient, setup):
    """Professional can sign a DRAFT exam request; status becomes SIGNED."""
    token = setup["token"]
    consult_id = setup["cr"].id

    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/exam-requests",
        json={"items": [{"exam_name": "Hemograma", "type": "LAB"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    resp = await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "SIGNED"
    assert data["signature_type"] == "SIMPLE"
    assert data["file_url"] is not None


@pytest.mark.asyncio
async def test_sign_nonexistent_document_returns_404(client: AsyncClient, setup):
    """Signing a non-existent document returns 404."""
    fake_id = uuid.uuid4()
    resp = await client.post(
        f"/professionals/me/documents/{fake_id}/sign", headers=_auth(setup["token"])
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sign_other_professionals_document_returns_403(
    client: AsyncClient, db_session: AsyncSession, setup
):
    """A professional cannot sign a document that belongs to another professional."""
    token = setup["token"]
    consult_id = setup["cr"].id

    # Original professional creates a prescription.
    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "X", "dosage": "1mg", "instructions": "once"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    # A different professional tries to sign it.
    spec = await _seed_specialty(db_session, slug="other-sign-test")
    await _seed_professional(db_session, "other_sign@doc.com", spec.id)
    resp = await client.post("/auth/login", json={"email": "other_sign@doc.com", "password": "pw"})
    other_token = resp.json()["access_token"]

    resp = await client.post(
        f"/professionals/me/documents/{doc_id}/sign", headers=_auth(other_token)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sign_already_signed_document_returns_422(client: AsyncClient, setup):
    """Signing a document that is already SIGNED returns 422."""
    token = setup["token"]
    consult_id = setup["cr"].id

    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "Dipirona", "dosage": "500mg", "instructions": "se dor"}]},
        headers=_auth(token),
    )
    doc_id = resp.json()["id"]

    # First sign – should succeed.
    resp = await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(token))
    assert resp.status_code == 200

    # Second sign – should fail.
    resp = await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(token))
    assert resp.status_code == 422


# ── Tests: F5 Part 2 – patient document access ────────────────────────────────


@pytest.fixture
async def patient_token(client: AsyncClient, setup) -> str:
    """Return a JWT for the patient created in the setup fixture."""
    resp = await client.post("/auth/login", json={"email": "patient@doc.com", "password": "pw"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_patient_can_list_signed_documents(
    client: AsyncClient, setup, patient_token: str
):
    """Patient can list SIGNED documents for their own consult."""
    pro_token = setup["token"]
    consult_id = setup["cr"].id

    # Professional creates and signs a prescription.
    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "Amoxicilina", "dosage": "500mg", "instructions": "8/8h"}]},
        headers=_auth(pro_token),
    )
    doc_id = resp.json()["id"]
    await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(pro_token))

    # Patient lists documents.
    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/documents",
        headers=_auth(patient_token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["status"] == "SIGNED"
    assert data[0]["file_url"] is not None


@pytest.mark.asyncio
async def test_patient_cannot_see_draft_documents(
    client: AsyncClient, setup, patient_token: str
):
    """Patient does not see DRAFT documents; only signed ones are returned."""
    pro_token = setup["token"]
    consult_id = setup["cr"].id

    # Create a prescription but do NOT sign it.
    await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "X", "dosage": "1mg", "instructions": "once"}]},
        headers=_auth(pro_token),
    )

    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/documents",
        headers=_auth(patient_token),
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_patient_can_get_single_signed_document(
    client: AsyncClient, setup, patient_token: str
):
    """Patient can retrieve a single signed document by its ID."""
    pro_token = setup["token"]
    consult_id = setup["cr"].id

    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "Paracetamol", "dosage": "750mg", "instructions": "se dor"}]},
        headers=_auth(pro_token),
    )
    doc_id = resp.json()["id"]
    await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(pro_token))

    resp = await client.get(f"/patients/me/documents/{doc_id}", headers=_auth(patient_token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == doc_id
    assert data["status"] == "SIGNED"
    assert data["file_url"] is not None


@pytest.mark.asyncio
async def test_patient_cannot_get_draft_document(
    client: AsyncClient, setup, patient_token: str
):
    """Patient gets 404 when trying to access an unsigned (DRAFT) document."""
    pro_token = setup["token"]
    consult_id = setup["cr"].id

    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "X", "dosage": "1mg", "instructions": "once"}]},
        headers=_auth(pro_token),
    )
    doc_id = resp.json()["id"]

    # DRAFT document → 404 for patient
    resp = await client.get(f"/patients/me/documents/{doc_id}", headers=_auth(patient_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patient_cannot_access_another_patients_document(
    client: AsyncClient, db_session: AsyncSession, setup, patient_token: str
):
    """Patient cannot access a document that belongs to a different patient."""
    pro_token = setup["token"]
    consult_id = setup["cr"].id

    # Sign a document for the original patient.
    resp = await client.post(
        f"/professionals/me/consult-requests/{consult_id}/prescriptions",
        json={"items": [{"drug_name": "X", "dosage": "1mg", "instructions": "once"}]},
        headers=_auth(pro_token),
    )
    doc_id = resp.json()["id"]
    await client.post(f"/professionals/me/documents/{doc_id}/sign", headers=_auth(pro_token))

    # A different patient tries to access it.
    other_patient_token, _ = await _register_and_login(
        client, "other_patient_access@doc.com", "patient"
    )
    resp = await client.get(
        f"/patients/me/documents/{doc_id}", headers=_auth(other_patient_token)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patient_cannot_list_documents_of_another_patients_consult(
    client: AsyncClient, db_session: AsyncSession, setup
):
    """Patient cannot list documents for a consult that belongs to another patient."""
    consult_id = setup["cr"].id
    other_patient_token, _ = await _register_and_login(
        client, "other_patient_list@doc.com", "patient"
    )
    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/documents",
        headers=_auth(other_patient_token),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_professional_cannot_use_patient_document_endpoints(
    client: AsyncClient, setup
):
    """Professional role is forbidden from the patient document endpoints."""
    pro_token = setup["token"]
    consult_id = setup["cr"].id

    resp = await client.get(
        f"/patients/me/consult-requests/{consult_id}/documents",
        headers=_auth(pro_token),
    )
    assert resp.status_code == 403
