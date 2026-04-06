"""Professional history service for F6 Part 2.

Aggregates ConsultRequest + Payment + ProfessionalPayout + MedicalDocument
records for a single professional into a paginated, filterable view-model.
Only read operations are performed; no state is mutated.

Design decisions
────────────────
* Mirrors the patient_history.py pattern (F6 Part 1) but scoped to the
  professional's perspective: ConsultRequest.matched_professional_user_id is
  the authorization boundary.

* Two-query strategy (same as F6 Part 1):
  1. Fetch matching ConsultRequests in one query.
  2. Batch-fetch Payments, ProfessionalPayouts, PatientProfiles and
     MedicalDocuments in separate queries keyed by consult_request_id /
     patient_user_id to avoid N+1 and Cartesian-product issues.

* ``file_url`` for documents is only returned when ``status == SIGNED``.

* ``_to_financial_status`` is reused from professional_financials to avoid
  duplicating the PaymentStatus → FinancialStatus mapping.

* Payout information is available via Payment.payout_id → ProfessionalPayout;
  a single batch query loads all relevant payout rows.

* For very large histories (> tens of thousands of consults), a future
  optimisation would push the ``has_payout`` and ``payment_status`` filters
  into the SQL query rather than doing them in-memory.  The current approach
  is correct and simple for the expected scale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import DocumentStatus, DocumentType, MedicalDocument
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.professional_payout import ProfessionalPayout
from app.services.professional_financials import FinancialStatus, _to_financial_status


# ── Domain data-classes ───────────────────────────────────────────────────────


@dataclass
class ProfessionalDocumentSummary:
    """Reduced view of a MedicalDocument for the professional history."""

    id: uuid.UUID
    document_type: DocumentType
    status: DocumentStatus
    created_at: datetime
    file_url: str | None
    summary: str


@dataclass
class ProfessionalPaymentSummary:
    """Reduced payment view for the professional history."""

    status: PaymentStatus
    financial_status: FinancialStatus
    amount_total_cents: int
    professional_amount_cents: int
    platform_fee_cents: int
    refunded_amount_cents: int


@dataclass
class ProfessionalPayoutSummary:
    """Payout information linked to a payment."""

    payout_id: uuid.UUID
    paid_out_at: datetime


@dataclass
class ProfessionalConsultHistoryItem:
    """Aggregated history item for one consult request (professional view)."""

    consult_id: uuid.UUID
    created_at: datetime
    scheduled_at: datetime | None
    status: ConsultRequestStatus
    specialty_id: uuid.UUID
    patient_name: str | None
    payment: ProfessionalPaymentSummary | None
    payout: ProfessionalPayoutSummary | None
    documents: list[ProfessionalDocumentSummary] = field(default_factory=list)


@dataclass
class ProfessionalConsultHistoryPage:
    """Paginated result wrapper."""

    items: list[ProfessionalConsultHistoryItem]
    total: int
    page: int
    limit: int


# ── Private helpers ───────────────────────────────────────────────────────────


def _build_payment_summary(payment: Payment) -> ProfessionalPaymentSummary:
    fs = _to_financial_status(payment.status)
    refunded = payment.amount_cents if fs == FinancialStatus.refunded else 0
    return ProfessionalPaymentSummary(
        status=payment.status,
        financial_status=fs,
        amount_total_cents=payment.amount_cents,
        professional_amount_cents=payment.professional_amount_cents,
        platform_fee_cents=payment.platform_fee_cents,
        refunded_amount_cents=refunded,
    )


def _build_doc_summary(doc: MedicalDocument) -> ProfessionalDocumentSummary:
    items: list = doc.content_json or []
    if doc.document_type == DocumentType.PRESCRIPTION:
        summary = items[0].get("drug_name", "") if items else ""
    else:
        summary = items[0].get("exam_name", "") if items else ""

    file_url = doc.file_url if doc.status == DocumentStatus.SIGNED else None
    return ProfessionalDocumentSummary(
        id=doc.id,
        document_type=doc.document_type,
        status=doc.status,
        created_at=doc.created_at,
        file_url=file_url,
        summary=summary,
    )


def _assemble_item(
    consult: ConsultRequest,
    patient_name: str | None,
    payment: Payment | None,
    payout: ProfessionalPayout | None,
    docs: list[MedicalDocument],
) -> ProfessionalConsultHistoryItem:
    payment_summary = _build_payment_summary(payment) if payment else None
    payout_summary: ProfessionalPayoutSummary | None = None
    if payout is not None:
        payout_summary = ProfessionalPayoutSummary(
            payout_id=payout.id,
            paid_out_at=payout.paid_out_at,
        )
    return ProfessionalConsultHistoryItem(
        consult_id=consult.id,
        created_at=consult.created_at,
        scheduled_at=consult.scheduled_at,
        status=consult.status,
        specialty_id=consult.specialty_id,
        patient_name=patient_name,
        payment=payment_summary,
        payout=payout_summary,
        documents=[_build_doc_summary(d) for d in docs],
    )


# ── Public service functions ──────────────────────────────────────────────────


async def list_professional_consult_history(
    professional_user_id: uuid.UUID,
    db: AsyncSession,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    consult_status: ConsultRequestStatus | None = None,
    payment_status: FinancialStatus | None = None,
    has_payout: bool | None = None,
    patient_name: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> ProfessionalConsultHistoryPage:
    """Return a paginated professional history aggregating consults, payments, payouts, and documents.

    Args:
        professional_user_id: The professional's user ID (authorization boundary).
        db: Async database session.
        from_date: Filter consults created on or after this date.
        to_date: Filter consults created on or before this date.
        consult_status: Optional single status filter.
        payment_status: Optional filter by FinancialStatus (mapped from PaymentStatus).
        has_payout: When True, return only consults whose payment has a payout_id;
            when False, only those without; None → no filter.
        patient_name: Case-insensitive substring filter on the patient's full_name.
        page: 1-based page number.
        limit: Results per page.

    Returns:
        ProfessionalConsultHistoryPage with items and pagination metadata.
    """
    if page < 1:
        page = 1

    # ── Step 1: fetch matching ConsultRequests ───────────────────────────────
    base_q = (
        select(ConsultRequest)
        .where(ConsultRequest.matched_professional_user_id == professional_user_id)
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
        return ProfessionalConsultHistoryPage(items=[], total=0, page=page, limit=limit)

    consult_ids = [c.id for c in all_consults]

    # ── Step 2: batch-fetch Payments ─────────────────────────────────────────
    pay_result = await db.execute(
        select(Payment).where(Payment.consult_request_id.in_(consult_ids))
    )
    payments_by_consult: dict[uuid.UUID, Payment] = {
        p.consult_request_id: p for p in pay_result.scalars().all()
    }

    # ── Step 3: batch-fetch ProfessionalPayouts ───────────────────────────────
    payout_ids = {
        p.payout_id
        for p in payments_by_consult.values()
        if p.payout_id is not None
    }
    payouts_by_id: dict[uuid.UUID, ProfessionalPayout] = {}
    if payout_ids:
        payout_result = await db.execute(
            select(ProfessionalPayout).where(ProfessionalPayout.id.in_(payout_ids))
        )
        payouts_by_id = {po.id: po for po in payout_result.scalars().all()}

    # ── Step 4: batch-fetch PatientProfiles ──────────────────────────────────
    patient_ids = {c.patient_user_id for c in all_consults}
    pat_result = await db.execute(
        select(PatientProfile).where(PatientProfile.user_id.in_(patient_ids))
    )
    patient_names_by_user: dict[uuid.UUID, str] = {
        pp.user_id: pp.full_name for pp in pat_result.scalars().all()
    }

    # ── Step 5: batch-fetch MedicalDocuments ─────────────────────────────────
    doc_result = await db.execute(
        select(MedicalDocument)
        .where(
            and_(
                MedicalDocument.consult_request_id.in_(consult_ids),
                MedicalDocument.professional_user_id == professional_user_id,
            )
        )
        .order_by(MedicalDocument.created_at)
    )
    docs_by_consult: dict[uuid.UUID, list[MedicalDocument]] = {}
    for doc in doc_result.scalars().all():
        docs_by_consult.setdefault(doc.consult_request_id, []).append(doc)

    # ── Step 6: apply in-memory filters ──────────────────────────────────────
    # payment_status, has_payout, and patient_name are applied here because
    # they span multiple tables; for large data sets these could be pushed to SQL.
    from app.services.professional_financials import _financial_status_to_payment_statuses  # noqa: PLC0415

    filtered_consults = all_consults

    if payment_status is not None:
        matching_pay_statuses = set(_financial_status_to_payment_statuses(payment_status))
        filtered_consults = [
            c for c in filtered_consults
            if (pay := payments_by_consult.get(c.id)) is not None
            and pay.status in matching_pay_statuses
        ]

    if has_payout is True:
        filtered_consults = [
            c for c in filtered_consults
            if (pay := payments_by_consult.get(c.id)) is not None and pay.payout_id is not None
        ]
    elif has_payout is False:
        filtered_consults = [
            c for c in filtered_consults
            if (pay := payments_by_consult.get(c.id)) is None or pay.payout_id is None
        ]

    if patient_name is not None:
        name_lower = patient_name.lower()
        filtered_consults = [
            c for c in filtered_consults
            if name_lower in (patient_names_by_user.get(c.patient_user_id) or "").lower()
        ]

    total = len(filtered_consults)

    # ── Step 7: paginate ──────────────────────────────────────────────────────
    offset = (page - 1) * limit
    page_consults = filtered_consults[offset : offset + limit]

    # ── Step 8: assemble items ────────────────────────────────────────────────
    items = []
    for c in page_consults:
        payment = payments_by_consult.get(c.id)
        payout: ProfessionalPayout | None = None
        if payment is not None and payment.payout_id is not None:
            payout = payouts_by_id.get(payment.payout_id)
        items.append(
            _assemble_item(
                consult=c,
                patient_name=patient_names_by_user.get(c.patient_user_id),
                payment=payment,
                payout=payout,
                docs=docs_by_consult.get(c.id, []),
            )
        )

    return ProfessionalConsultHistoryPage(items=items, total=total, page=page, limit=limit)


async def get_professional_consult_detail(
    professional_user_id: uuid.UUID,
    consult_id: uuid.UUID,
    db: AsyncSession,
) -> ProfessionalConsultHistoryItem | None:
    """Return a single history item for one consult, or None if not found / not owned.

    Args:
        professional_user_id: The professional's user ID (authorization boundary).
        consult_id: The consult request ID to retrieve.
        db: Async database session.

    Returns:
        ProfessionalConsultHistoryItem or None if not found / not owned by professional.
    """
    result = await db.execute(
        select(ConsultRequest).where(
            ConsultRequest.id == consult_id,
            ConsultRequest.matched_professional_user_id == professional_user_id,
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

    # Payout
    payout: ProfessionalPayout | None = None
    if payment is not None and payment.payout_id is not None:
        payout_result = await db.execute(
            select(ProfessionalPayout).where(ProfessionalPayout.id == payment.payout_id)
        )
        payout = payout_result.scalar_one_or_none()

    # Patient profile
    pat_result = await db.execute(
        select(PatientProfile).where(PatientProfile.user_id == consult.patient_user_id)
    )
    patient_profile = pat_result.scalar_one_or_none()
    patient_name = patient_profile.full_name if patient_profile else None

    # Documents
    doc_result = await db.execute(
        select(MedicalDocument)
        .where(
            MedicalDocument.consult_request_id == consult_id,
            MedicalDocument.professional_user_id == professional_user_id,
        )
        .order_by(MedicalDocument.created_at)
    )
    docs = list(doc_result.scalars().all())

    return _assemble_item(
        consult=consult,
        patient_name=patient_name,
        payment=payment,
        payout=payout,
        docs=docs,
    )
