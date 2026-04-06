"""Patient history service for F6 Part 1.

Aggregates ConsultRequest + Payment + MedicalDocument records for a single
patient into a paginated, filterable view-model.  Only read operations are
performed; no state is mutated.

Design decisions
────────────────
* One JOINed query fetches ConsultRequest + Payment + ProfessionalProfile in a
  single round-trip.  MedicalDocuments are fetched in a second batch query
  (keyed by consult_request_id) to avoid a Cartesian product with multiple
  LEFT JOINs returning repeated rows.
* Payment method is not stored in our DB (Pagar.me holds it); the ``method``
  field is therefore always ``None``.  The field is retained in the schema so
  that a future gateway callback can populate it without breaking the contract.
* ``file_url`` for documents is only included when ``status == SIGNED``; draft
  documents expose ``file_url=None`` to the patient.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import DocumentStatus, DocumentType, MedicalDocument
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.professional_profile import ProfessionalProfile
from app.services.professional_financials import _to_financial_status, FinancialStatus


# ── Domain data-classes ───────────────────────────────────────────────────────


@dataclass
class PatientDocumentSummary:
    """Reduced view of a MedicalDocument for the patient history."""

    id: uuid.UUID
    document_type: DocumentType
    status: DocumentStatus
    created_at: datetime
    file_url: str | None
    summary: str


@dataclass
class PatientPaymentSummary:
    """Reduced payment view for the patient history."""

    status: PaymentStatus
    financial_status: FinancialStatus
    amount_total_cents: int
    refunded_amount_cents: int
    method: str | None  # not stored locally; reserved for future use


@dataclass
class PatientConsultHistoryItem:
    """Aggregated history item for one consult request."""

    consult_id: uuid.UUID
    created_at: datetime
    scheduled_at: datetime | None
    status: ConsultRequestStatus
    specialty: str
    professional_name: str | None
    professional_specialty: str | None
    professional_crm: str | None
    payment: PatientPaymentSummary | None
    documents: list[PatientDocumentSummary] = field(default_factory=list)


@dataclass
class PatientConsultHistoryPage:
    """Paginated result wrapper."""

    items: list[PatientConsultHistoryItem]
    total: int
    page: int
    limit: int


# ── Private helpers ───────────────────────────────────────────────────────────


def _build_payment_summary(payment: Payment) -> PatientPaymentSummary:
    fs = _to_financial_status(payment.status)
    refunded = payment.amount_cents if fs == FinancialStatus.refunded else 0
    return PatientPaymentSummary(
        status=payment.status,
        financial_status=fs,
        amount_total_cents=payment.amount_cents,
        refunded_amount_cents=refunded,
        method=None,
    )


def _build_doc_summary(doc: MedicalDocument) -> PatientDocumentSummary:
    items: list = doc.content_json or []
    if doc.document_type == DocumentType.PRESCRIPTION:
        summary = items[0].get("drug_name", "") if items else ""
    else:
        summary = items[0].get("exam_name", "") if items else ""

    file_url = doc.file_url if doc.status == DocumentStatus.SIGNED else None
    return PatientDocumentSummary(
        id=doc.id,
        document_type=doc.document_type,
        status=doc.status,
        created_at=doc.created_at,
        file_url=file_url,
        summary=summary,
    )


def _assemble_item(
    consult: ConsultRequest,
    pro_profile: ProfessionalProfile | None,
    payment: Payment | None,
    docs: list[MedicalDocument],
) -> PatientConsultHistoryItem:
    return PatientConsultHistoryItem(
        consult_id=consult.id,
        created_at=consult.created_at,
        scheduled_at=consult.scheduled_at,
        status=consult.status,
        specialty=str(consult.specialty_id),  # specialty_id used as slug fallback
        professional_name=pro_profile.full_name if pro_profile else None,
        professional_specialty=pro_profile.specialty if pro_profile else None,
        professional_crm=pro_profile.crm if pro_profile else None,
        payment=_build_payment_summary(payment) if payment else None,
        documents=[_build_doc_summary(d) for d in docs],
    )


# ── Public service functions ──────────────────────────────────────────────────


async def list_patient_consult_history(
    patient_user_id: uuid.UUID,
    db: AsyncSession,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    consult_status: ConsultRequestStatus | None = None,
    has_documents: bool | None = None,
    page: int = 1,
    limit: int = 20,
) -> PatientConsultHistoryPage:
    """Return a paginated patient history aggregating consults, payments, and documents.

    Args:
        patient_user_id: The patient's user ID (authorization boundary).
        db: Async database session.
        from_date: Filter consults created on or after this date.
        to_date: Filter consults created on or before this date.
        consult_status: Optional single status filter.
        has_documents: When True, return only consults with ≥1 document;
            when False, only consults with no documents; None → no filter.
        page: 1-based page number.
        limit: Results per page.

    Returns:
        PatientConsultHistoryPage with items and pagination metadata.
    """
    if page < 1:
        page = 1

    # ── Step 1: fetch matching ConsultRequests + optional Payment JOIN ──────────
    base_q = (
        select(ConsultRequest)
        .where(ConsultRequest.patient_user_id == patient_user_id)
    )

    if from_date is not None:
        base_q = base_q.where(ConsultRequest.created_at >= from_date)
    if to_date is not None:
        base_q = base_q.where(ConsultRequest.created_at <= to_date)
    if consult_status is not None:
        base_q = base_q.where(ConsultRequest.status == consult_status)

    result = await db.execute(base_q.order_by(ConsultRequest.created_at.desc()))
    all_consults: list[ConsultRequest] = list(result.scalars().all())

    if not all_consults:
        return PatientConsultHistoryPage(items=[], total=0, page=page, limit=limit)

    consult_ids = [c.id for c in all_consults]

    # ── Step 2: batch-fetch Payments ────────────────────────────────────────────
    pay_result = await db.execute(
        select(Payment).where(Payment.consult_request_id.in_(consult_ids))
    )
    payments_by_consult: dict[uuid.UUID, Payment] = {
        p.consult_request_id: p for p in pay_result.scalars().all()
    }

    # ── Step 3: batch-fetch ProfessionalProfiles ────────────────────────────────
    matched_pro_ids = {
        c.matched_professional_user_id
        for c in all_consults
        if c.matched_professional_user_id is not None
    }
    profiles_by_user: dict[uuid.UUID, ProfessionalProfile] = {}
    if matched_pro_ids:
        pro_result = await db.execute(
            select(ProfessionalProfile).where(
                ProfessionalProfile.user_id.in_(matched_pro_ids)
            )
        )
        profiles_by_user = {p.user_id: p for p in pro_result.scalars().all()}

    # ── Step 4: batch-fetch MedicalDocuments ────────────────────────────────────
    doc_result = await db.execute(
        select(MedicalDocument)
        .where(
            and_(
                MedicalDocument.consult_request_id.in_(consult_ids),
                MedicalDocument.patient_user_id == patient_user_id,
            )
        )
        .order_by(MedicalDocument.created_at)
    )
    docs_by_consult: dict[uuid.UUID, list[MedicalDocument]] = {}
    for doc in doc_result.scalars().all():
        docs_by_consult.setdefault(doc.consult_request_id, []).append(doc)

    # ── Step 5: apply has_documents filter (in-memory after doc fetch) ──────────
    filtered_consults = all_consults
    if has_documents is True:
        filtered_consults = [c for c in all_consults if docs_by_consult.get(c.id)]
    elif has_documents is False:
        filtered_consults = [c for c in all_consults if not docs_by_consult.get(c.id)]

    total = len(filtered_consults)

    # ── Step 6: paginate ─────────────────────────────────────────────────────────
    offset = (page - 1) * limit
    page_consults = filtered_consults[offset : offset + limit]

    # ── Step 7: assemble items ───────────────────────────────────────────────────
    items = [
        _assemble_item(
            consult=c,
            pro_profile=profiles_by_user.get(c.matched_professional_user_id)
            if c.matched_professional_user_id
            else None,
            payment=payments_by_consult.get(c.id),
            docs=docs_by_consult.get(c.id, []),
        )
        for c in page_consults
    ]

    return PatientConsultHistoryPage(items=items, total=total, page=page, limit=limit)


async def get_patient_consult_detail(
    patient_user_id: uuid.UUID,
    consult_id: uuid.UUID,
    db: AsyncSession,
) -> PatientConsultHistoryItem | None:
    """Return a single history item for one consult, or None if not found/not owned.

    Args:
        patient_user_id: The patient's user ID (authorization boundary).
        consult_id: The consult request ID to retrieve.
        db: Async database session.

    Returns:
        PatientConsultHistoryItem or None if not found / not owned by patient.
    """
    result = await db.execute(
        select(ConsultRequest).where(
            ConsultRequest.id == consult_id,
            ConsultRequest.patient_user_id == patient_user_id,
        )
    )
    consult = result.scalar_one_or_none()
    if consult is None:
        return None

    # Payment
    pay_result = await db.execute(
        select(Payment).where(Payment.consult_request_id == consult_id)
    )
    payment = pay_result.scalar_one_or_none()

    # Professional profile
    pro_profile: ProfessionalProfile | None = None
    if consult.matched_professional_user_id is not None:
        pro_result = await db.execute(
            select(ProfessionalProfile).where(
                ProfessionalProfile.user_id == consult.matched_professional_user_id
            )
        )
        pro_profile = pro_result.scalar_one_or_none()

    # Documents
    doc_result = await db.execute(
        select(MedicalDocument)
        .where(
            MedicalDocument.consult_request_id == consult_id,
            MedicalDocument.patient_user_id == patient_user_id,
        )
        .order_by(MedicalDocument.created_at)
    )
    docs = list(doc_result.scalars().all())

    return _assemble_item(consult=consult, pro_profile=pro_profile, payment=payment, docs=docs)
