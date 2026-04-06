"""Service layer for F5 Part 1 & Part 2 – Medical Documents.

Handles creation, retrieval, and signing of prescriptions and exam requests
linked to a ConsultRequest.  All business-logic validation (ownership, consult
status) lives here so the API layer stays thin.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import (
    DocumentStatus,
    DocumentSubtype,
    DocumentType,
    MedicalDocument,
    SignatureType,
)
from app.db.models.patient_profile import PatientProfile
from app.db.models.professional_profile import ProfessionalProfile
from app.db.models.user import User
from app.schemas.schemas import (
    ExamRequestCreate,
    ExamRequestItem,
    MedicalDocumentResponse,
    PrescriptionCreate,
    PrescriptionItem,
)

# ConsultRequest statuses that allow document creation.
_ALLOWED_STATUSES = {
    ConsultRequestStatus.matched,
    ConsultRequestStatus.no_show_patient,
    # completed / finished states are handled via the matched lifecycle;
    # no_show_patient is included so the doctor can still issue documents
    # after a no-show has been recorded.
}


def _build_summary(document_type: DocumentType, items: list[dict]) -> str:
    """Return a short human-readable description of the document."""
    if not items:
        return ""
    first = items[0]
    if document_type == DocumentType.PRESCRIPTION:
        return first.get("drug_name", "")
    return first.get("exam_name", "")


def _to_response(doc: MedicalDocument) -> MedicalDocumentResponse:
    items: list[Any] = doc.content_json or []
    return MedicalDocumentResponse(
        id=doc.id,
        consult_request_id=doc.consult_request_id,
        professional_user_id=doc.professional_user_id,
        patient_user_id=doc.patient_user_id,
        document_type=doc.document_type,
        subtype=doc.subtype,
        status=doc.status,
        signature_type=doc.signature_type,
        signed_at=doc.signed_at,
        file_url=doc.file_url,
        content=items,
        summary=_build_summary(doc.document_type, items),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


async def _load_and_authorise_consult(
    db: AsyncSession,
    consult_id: uuid.UUID,
    professional: User,
) -> ConsultRequest:
    """Load the ConsultRequest and verify it belongs to the professional.

    Raises 404 if not found, 403 if the professional is not the owner,
    and 422 if the consult is in a terminal/cancelled state.
    """
    result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == consult_id)
    )
    consult = result.scalar_one_or_none()
    if consult is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consult request not found",
        )
    if consult.matched_professional_user_id != professional.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the professional for this consult request",
        )
    return consult


async def create_prescription_for_consult(
    db: AsyncSession,
    consult_id: uuid.UUID,
    professional: User,
    payload: PrescriptionCreate,
) -> MedicalDocumentResponse:
    """Create a PRESCRIPTION document linked to a consult request."""
    consult = await _load_and_authorise_consult(db, consult_id, professional)

    if consult.status not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Cannot create documents for a consult in '{consult.status}' status. "
                f"Allowed statuses: {[s.value for s in _ALLOWED_STATUSES]}"
            ),
        )

    items_data = [item.model_dump() for item in payload.items]

    doc = MedicalDocument(
        id=uuid.uuid4(),
        consult_request_id=consult.id,
        professional_user_id=professional.id,
        patient_user_id=consult.patient_user_id,
        document_type=DocumentType.PRESCRIPTION,
        subtype=None,
        content_json=items_data,
        status=DocumentStatus.DRAFT,
        signature_type=SignatureType.NONE,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _to_response(doc)


async def create_exam_request_for_consult(
    db: AsyncSession,
    consult_id: uuid.UUID,
    professional: User,
    payload: ExamRequestCreate,
) -> MedicalDocumentResponse:
    """Create an EXAM_REQUEST document linked to a consult request.

    The subtype is inferred from the item list:
      - All LAB items → LAB
      - All IMAGING items → IMAGING
      - Mixed → None (subtype left unset)
    """
    consult = await _load_and_authorise_consult(db, consult_id, professional)

    if consult.status not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Cannot create documents for a consult in '{consult.status}' status. "
                f"Allowed statuses: {[s.value for s in _ALLOWED_STATUSES]}"
            ),
        )

    items_data = [item.model_dump() for item in payload.items]

    types = {item.type for item in payload.items}
    if types == {DocumentSubtype.LAB}:
        subtype: DocumentSubtype | None = DocumentSubtype.LAB
    elif types == {DocumentSubtype.IMAGING}:
        subtype = DocumentSubtype.IMAGING
    else:
        subtype = None

    doc = MedicalDocument(
        id=uuid.uuid4(),
        consult_request_id=consult.id,
        professional_user_id=professional.id,
        patient_user_id=consult.patient_user_id,
        document_type=DocumentType.EXAM_REQUEST,
        subtype=subtype,
        content_json=items_data,
        status=DocumentStatus.DRAFT,
        signature_type=SignatureType.NONE,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return _to_response(doc)


async def list_documents_for_consult(
    db: AsyncSession,
    consult_id: uuid.UUID,
    professional: User,
) -> list[MedicalDocumentResponse]:
    """List all documents (prescriptions + exam requests) for a consult."""
    await _load_and_authorise_consult(db, consult_id, professional)

    result = await db.execute(
        select(MedicalDocument)
        .where(MedicalDocument.consult_request_id == consult_id)
        .order_by(MedicalDocument.created_at)
    )
    docs = result.scalars().all()
    return [_to_response(doc) for doc in docs]


# ── F5 Part 2 – Signing & Patient access ──────────────────────────────────────


async def sign_medical_document(
    db: AsyncSession,
    document_id: uuid.UUID,
    professional: User,
) -> MedicalDocumentResponse:
    """Sign a DRAFT document, generate a PDF and store it.

    Transitions: DRAFT → SIGNED.
    Populates: ``signature_type = SIMPLE``, ``signed_at``, ``file_url``.

    Raises:
        404 – document not found.
        403 – authenticated professional does not own the document.
        422 – document is not in DRAFT status.
    """
    # Import here to avoid circular deps and to keep the heavy PDF/IO imports
    # out of the module-level namespace where they are not always needed.
    from app.services.pdf_generator import generate_medical_document_pdf  # noqa: PLC0415
    from app.utils.file_storage import save_document_file  # noqa: PLC0415

    # Load the document together with related records needed for the PDF.
    result = await db.execute(
        select(MedicalDocument)
        .options(selectinload(MedicalDocument.consult_request))
        .where(MedicalDocument.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if doc.professional_user_id != professional.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the professional for this document",
        )

    if doc.status != DocumentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Only DRAFT documents can be signed; current status is '{doc.status}'",
        )

    # Load professional profile for PDF metadata.
    pro_result = await db.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == professional.id)
    )
    pro_profile = pro_result.scalar_one_or_none()
    professional_name = pro_profile.full_name if pro_profile else professional.email
    professional_crm = pro_profile.crm if pro_profile else "TODO: CRM not set"
    professional_specialty = pro_profile.specialty if pro_profile else "TODO: specialty not set"

    # Load patient profile for PDF metadata.
    pat_result = await db.execute(
        select(PatientProfile).where(PatientProfile.user_id == doc.patient_user_id)
    )
    pat_profile = pat_result.scalar_one_or_none()
    patient_name = pat_profile.full_name if pat_profile else str(doc.patient_user_id)
    patient_cpf = pat_profile.cpf if pat_profile else None
    patient_dob = (
        pat_profile.date_of_birth.strftime("%d/%m/%Y")
        if pat_profile and pat_profile.date_of_birth
        else None
    )

    # Determine consult date.
    consult = doc.consult_request
    if consult and consult.scheduled_at:
        consult_date = consult.scheduled_at.strftime("%d/%m/%Y %H:%M UTC")
    else:
        consult_date = "Data não disponível"

    signed_at = datetime.now(tz=timezone.utc)

    # Generate PDF bytes.
    pdf_bytes = generate_medical_document_pdf(
        document=doc,
        professional_name=professional_name,
        professional_crm=professional_crm,
        professional_specialty=professional_specialty,
        patient_name=patient_name,
        patient_cpf=patient_cpf,
        patient_dob=patient_dob,
        consult_date=consult_date,
        signed_at=signed_at,
    )

    # Persist the file and update the document.
    file_url = save_document_file(doc.id, pdf_bytes)

    doc.status = DocumentStatus.SIGNED
    doc.signature_type = SignatureType.SIMPLE
    doc.signed_at = signed_at
    doc.file_url = file_url

    await db.commit()
    await db.refresh(doc)
    return _to_response(doc)


async def list_documents_for_patient(
    db: AsyncSession,
    consult_id: uuid.UUID,
    patient: User,
) -> list[MedicalDocumentResponse]:
    """List SIGNED documents for a consult visible to the patient.

    Only signed documents are returned; DRAFT documents are not exposed to
    patients until the professional has signed them.

    Raises:
        403 – the consult does not belong to this patient.
        404 – the consult does not exist.
    """
    # Verify consult exists and belongs to this patient.
    cr_result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == consult_id)
    )
    consult = cr_result.scalar_one_or_none()
    if consult is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found")
    if consult.patient_user_id != patient.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This consult request does not belong to you",
        )

    result = await db.execute(
        select(MedicalDocument)
        .where(
            MedicalDocument.consult_request_id == consult_id,
            MedicalDocument.status == DocumentStatus.SIGNED,
        )
        .order_by(MedicalDocument.signed_at)
    )
    docs = result.scalars().all()
    return [_to_response(doc) for doc in docs]


async def get_document_for_patient(
    db: AsyncSession,
    document_id: uuid.UUID,
    patient: User,
) -> MedicalDocumentResponse:
    """Return a single SIGNED document accessible to the patient.

    Raises:
        404 – document not found or not SIGNED.
        403 – document does not belong to this patient.
    """
    result = await db.execute(
        select(MedicalDocument).where(MedicalDocument.id == document_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None or doc.status != DocumentStatus.SIGNED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.patient_user_id != patient.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This document does not belong to you",
        )
    return _to_response(doc)
