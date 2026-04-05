"""Admin financial view service (F4 Part 4B).

Provides a platform-wide financial summary and transaction list for
administrative use, reusing the FinancialStatus mapping defined in
professional_financials.py.

All values are computed on-the-fly from stored Payment columns; no extra
aggregation tables are required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consult_request import ConsultRequest
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.professional_payout import ProfessionalPayout
from app.services.professional_financials import (
    FinancialStatus,
    _financial_status_to_payment_statuses,
    _to_financial_status,
)


# ── Domain data-classes ───────────────────────────────────────────────────────


@dataclass
class AdminFinancialSummary:
    """Platform-wide aggregated financial totals (all in cents)."""

    total_payments_cents: int
    total_platform_fees_cents: int
    total_professional_amount_cents: int
    total_refunded_cents: int


@dataclass
class AdminFinancialTransaction:
    """Single transaction entry for the admin financial list."""

    payment_id: uuid.UUID
    consult_request_id: uuid.UUID
    patient_user_id: uuid.UUID
    professional_user_id: uuid.UUID | None
    amount_total_cents: int
    platform_fee_cents: int
    professional_amount_cents: int
    financial_status: FinancialStatus
    payout_id: uuid.UUID | None
    created_at: datetime


# ── Payout result data-classes ────────────────────────────────────────────────


@dataclass
class ProfessionalPayoutSummary:
    professional_user_id: uuid.UUID
    payout_id: uuid.UUID
    total_professional_amount_cents: int
    payment_count: int


@dataclass
class AdminPayoutResult:
    payouts_created: int
    payments_included: int
    already_paid: int
    professional_summaries: list[ProfessionalPayoutSummary]


# ── Service functions ─────────────────────────────────────────────────────────


async def get_admin_financial_summary(
    db: AsyncSession,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> AdminFinancialSummary:
    """Compute a platform-wide financial summary.

    Only payments with status ``paid`` contribute to
    ``total_payments_cents``, ``total_platform_fees_cents``, and
    ``total_professional_amount_cents``.  Payments with status
    ``refunded`` contribute to ``total_refunded_cents``.

    Args:
        db: Async database session.
        from_date: Include only payments created on or after this datetime.
        to_date: Include only payments created on or before this datetime.

    Returns:
        AdminFinancialSummary with aggregated totals in cents.
    """
    stmt = select(Payment)
    if from_date is not None:
        stmt = stmt.where(Payment.created_at >= from_date)
    if to_date is not None:
        stmt = stmt.where(Payment.created_at <= to_date)

    result = await db.execute(stmt)
    payments = result.scalars().all()

    total_payments_cents = 0
    total_platform_fees_cents = 0
    total_professional_amount_cents = 0
    total_refunded_cents = 0

    for payment in payments:
        fs = _to_financial_status(payment.status)
        if fs == FinancialStatus.paid:
            total_payments_cents += payment.amount_cents
            total_platform_fees_cents += payment.platform_fee_cents
            total_professional_amount_cents += payment.professional_amount_cents
        elif fs == FinancialStatus.refunded:
            total_refunded_cents += payment.amount_cents

    return AdminFinancialSummary(
        total_payments_cents=total_payments_cents,
        total_platform_fees_cents=total_platform_fees_cents,
        total_professional_amount_cents=total_professional_amount_cents,
        total_refunded_cents=total_refunded_cents,
    )


async def list_admin_financial_transactions(
    db: AsyncSession,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    financial_status: FinancialStatus | None = None,
    professional_user_id: uuid.UUID | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[AdminFinancialTransaction], int]:
    """List paginated financial transactions for the admin view.

    A single JOIN query fetches Payment + ConsultRequest together to avoid
    N+1 queries.

    Args:
        db: Async database session.
        from_date: Include only payments created on or after this datetime.
        to_date: Include only payments created on or before this datetime.
        financial_status: Optional filter by financial status.
        professional_user_id: Optional filter to a specific professional.
        page: 1-based page number.
        limit: Maximum items per page.

    Returns:
        Tuple of ``(items, total_count)``.
    """
    if page < 1:
        page = 1

    base_q = select(Payment).join(
        ConsultRequest, Payment.consult_request_id == ConsultRequest.id
    )

    if from_date is not None:
        base_q = base_q.where(Payment.created_at >= from_date)
    if to_date is not None:
        base_q = base_q.where(Payment.created_at <= to_date)
    if financial_status is not None:
        matching = _financial_status_to_payment_statuses(financial_status)
        base_q = base_q.where(Payment.status.in_(matching))
    if professional_user_id is not None:
        base_q = base_q.where(Payment.professional_user_id == professional_user_id)

    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total = count_result.scalar_one()

    offset = (page - 1) * limit
    data_result = await db.execute(
        base_q.order_by(Payment.created_at.desc()).offset(offset).limit(limit)
    )
    payments = data_result.scalars().all()

    items = [
        AdminFinancialTransaction(
            payment_id=p.id,
            consult_request_id=p.consult_request_id,
            patient_user_id=p.patient_user_id,
            professional_user_id=p.professional_user_id,
            amount_total_cents=p.amount_cents,
            platform_fee_cents=p.platform_fee_cents,
            professional_amount_cents=p.professional_amount_cents,
            financial_status=_to_financial_status(p.status),
            payout_id=p.payout_id,
            created_at=p.created_at,
        )
        for p in payments
    ]

    return items, total


async def create_payouts(
    payment_ids: list[uuid.UUID],
    db: AsyncSession,
) -> AdminPayoutResult:
    """Mark a list of payments as paid out to their respective professionals.

    Behaviour:
    - Groups the supplied payment_ids by professional_user_id.
    - For each professional, creates a ProfessionalPayout record summing
      professional_amount_cents of the eligible payments.
    - Links each eligible Payment to its new payout via Payment.payout_id.
    - Payments that already have a payout_id are silently skipped
      (idempotency).
    - Payments that do not have a professional_user_id are silently skipped.

    Args:
        payment_ids: List of Payment UUIDs to include in this payout run.
        db: Async database session.

    Returns:
        AdminPayoutResult summarising what was created.
    """
    if not payment_ids:
        return AdminPayoutResult(
            payouts_created=0,
            payments_included=0,
            already_paid=0,
            professional_summaries=[],
        )

    result = await db.execute(
        select(Payment).where(Payment.id.in_(payment_ids))
    )
    payments = result.scalars().all()

    # Separate already-paid from eligible
    already_paid_count = 0
    # professional_user_id → list of Payment
    by_professional: dict[uuid.UUID, list[Payment]] = {}

    for p in payments:
        if p.payout_id is not None:
            already_paid_count += 1
            continue
        if p.professional_user_id is None:
            continue
        by_professional.setdefault(p.professional_user_id, []).append(p)

    summaries: list[ProfessionalPayoutSummary] = []
    total_included = 0

    for prof_id, prof_payments in by_professional.items():
        total_cents = sum(pp.professional_amount_cents for pp in prof_payments)
        payout = ProfessionalPayout(
            id=uuid.uuid4(),
            professional_user_id=prof_id,
            total_amount_cents=total_cents,
        )
        db.add(payout)
        await db.flush()  # populate payout.id before referencing it

        for pp in prof_payments:
            pp.payout_id = payout.id

        summaries.append(
            ProfessionalPayoutSummary(
                professional_user_id=prof_id,
                payout_id=payout.id,
                total_professional_amount_cents=total_cents,
                payment_count=len(prof_payments),
            )
        )
        total_included += len(prof_payments)

    await db.commit()

    return AdminPayoutResult(
        payouts_created=len(summaries),
        payments_included=total_included,
        already_paid=already_paid_count,
        professional_summaries=summaries,
    )
