"""Service layer for F5 Part 1 – Medical Documents.

Handles creation and retrieval of prescriptions and exam requests linked to a
ConsultRequest.  All business-logic validation (ownership, consult status) lives
here so the API layer stays thin.

TODO (F5 Part 2):
  - Add ``sign_document()`` to transition status DRAFT → SIGNED, populate
    ``signed_at`` and ``signature_type`` (SIMPLE or ICP_BRASIL).
  - Add ``generate_pdf()`` to produce a PDF and store its URL in ``file_url``.
"""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import (
    DocumentStatus,
    DocumentSubtype,
    DocumentType,
    MedicalDocument,
    SignatureType,
)
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
