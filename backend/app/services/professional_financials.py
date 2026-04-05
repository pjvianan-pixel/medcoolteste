"""Financial statement service for professionals (F4 Part 4A).

Computes the professional's financial summary and paginated transaction list
from Payment + ConsultRequest records.  All values are calculated on-the-fly
from stored columns; no additional tables are required.

Financial status mapping
────────────────────────
PaymentStatus.paid             → FinancialStatus.paid
    Professional's split has been (or will be) transferred.

PaymentStatus.refund_pending   → FinancialStatus.refund_pending
    A refund was requested; the professional's share is in limbo until the
    gateway confirms the outcome.  Included in ``total_pending`` to signal
    that the amount has not been definitively lost yet.

PaymentStatus.refunded         → FinancialStatus.refunded
    Refund completed; the professional does not receive their split.

PaymentStatus.pending          → FinancialStatus.pending
    Payment created but not yet captured by the gateway.

PaymentStatus.processing       → FinancialStatus.pending
    Payment is being processed by the gateway.

PaymentStatus.failed           → FinancialStatus.canceled
    Gateway charge failed; no transfer to the professional.

PaymentStatus.canceled         → FinancialStatus.canceled
    Payment was canceled before capture; no transfer to the professional.

Summary totals
──────────────
total_received   – sum of professional_amount_cents for "paid" transactions.
total_pending    – sum of professional_amount_cents for "pending" and
                   "refund_pending" transactions (amount not yet settled).
total_refunded   – sum of professional_amount_cents for "refunded" transactions.

This mapping and the summary logic are intentionally documented here because
they will also drive the payout logic in F4 Part 4B.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consult_request import ConsultRequest
from app.db.models.payment import Payment, PaymentStatus


class FinancialStatus(str, enum.Enum):
    """Simplified financial status exposed to the professional."""

    pending = "pending"
    paid = "paid"
    refund_pending = "refund_pending"
    refunded = "refunded"
    canceled = "canceled"


# ── Status mapping helpers ────────────────────────────────────────────────────

_PAYMENT_TO_FINANCIAL: dict[PaymentStatus, FinancialStatus] = {
    PaymentStatus.paid: FinancialStatus.paid,
    PaymentStatus.refund_pending: FinancialStatus.refund_pending,
    PaymentStatus.refunded: FinancialStatus.refunded,
    PaymentStatus.pending: FinancialStatus.pending,
    PaymentStatus.processing: FinancialStatus.pending,
    PaymentStatus.failed: FinancialStatus.canceled,
    PaymentStatus.canceled: FinancialStatus.canceled,
}


def _to_financial_status(payment_status: PaymentStatus) -> FinancialStatus:
    """Map a PaymentStatus to the professional-facing FinancialStatus."""
    return _PAYMENT_TO_FINANCIAL[payment_status]


def _financial_status_to_payment_statuses(fs: FinancialStatus) -> list[PaymentStatus]:
    """Return all PaymentStatus values that map to the given FinancialStatus."""
    return [ps for ps, mapped in _PAYMENT_TO_FINANCIAL.items() if mapped == fs]


# ── Domain data-classes ───────────────────────────────────────────────────────


@dataclass
class ProfessionalTransaction:
    """A single financial transaction entry for the professional's statement."""

    consult_request_id: uuid.UUID
    payment_id: uuid.UUID
    scheduled_at: datetime | None
    created_at: datetime
    amount_total: int
    platform_fee_amount: int
    professional_amount: int
    financial_status: FinancialStatus


@dataclass
class ProfessionalFinancialSummary:
    """Aggregated financial summary for a professional."""

    total_received: int
    """Sum of professional_amount_cents for paid transactions (cents)."""

    total_pending: int
    """Sum of professional_amount_cents for pending/refund_pending transactions (cents)."""

    total_refunded: int
    """Sum of professional_amount_cents for refunded transactions (cents)."""


# ── Service functions ─────────────────────────────────────────────────────────


async def get_professional_financial_summary(
    professional_user_id: uuid.UUID,
    db: AsyncSession,
) -> ProfessionalFinancialSummary:
    """Compute the financial summary for a professional.

    Fetches all payments for the professional and aggregates them by
    FinancialStatus.  Canceled payments are ignored in the summary.

    Args:
        professional_user_id: The professional's user ID.
        db: Async database session.

    Returns:
        ProfessionalFinancialSummary with totals in cents.
    """
    result = await db.execute(
        select(Payment)
        .join(ConsultRequest, Payment.consult_request_id == ConsultRequest.id)
        .where(Payment.professional_user_id == professional_user_id)
    )
    payments = result.scalars().all()

    total_received = 0
    total_pending = 0
    total_refunded = 0

    for payment in payments:
        fs = _to_financial_status(payment.status)
        amount = payment.professional_amount_cents
        if fs == FinancialStatus.paid:
            total_received += amount
        elif fs in (FinancialStatus.pending, FinancialStatus.refund_pending):
            total_pending += amount
        elif fs == FinancialStatus.refunded:
            total_refunded += amount
        # canceled: excluded from all totals

    return ProfessionalFinancialSummary(
        total_received=total_received,
        total_pending=total_pending,
        total_refunded=total_refunded,
    )


async def list_professional_transactions(
    professional_user_id: uuid.UUID,
    db: AsyncSession,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    financial_status: FinancialStatus | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[ProfessionalTransaction], int]:
    """List paginated financial transactions for a professional.

    A single JOIN query is used to fetch Payment + ConsultRequest.scheduled_at
    together, avoiding N+1 queries.

    Args:
        professional_user_id: The professional's user ID.
        db: Async database session.
        from_date: Include only payments created on or after this datetime.
        to_date: Include only payments created on or before this datetime.
        financial_status: Optional filter by financial status.
        page: 1-based page number.
        limit: Maximum number of items per page.

    Returns:
        Tuple of ``(items, total_count)`` where total_count is the unfiltered
        total matching the applied filters.
    """
    if page < 1:
        page = 1

    # Build shared WHERE conditions for both count and data queries
    base_q = (
        select(Payment, ConsultRequest.scheduled_at.label("scheduled_at"))
        .join(ConsultRequest, Payment.consult_request_id == ConsultRequest.id)
        .where(Payment.professional_user_id == professional_user_id)
    )

    if from_date is not None:
        base_q = base_q.where(Payment.created_at >= from_date)
    if to_date is not None:
        base_q = base_q.where(Payment.created_at <= to_date)
    if financial_status is not None:
        matching = _financial_status_to_payment_statuses(financial_status)
        base_q = base_q.where(Payment.status.in_(matching))

    # Total count (use subquery to re-use all applied filters)
    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total = count_result.scalar_one()

    # Paginated data
    offset = (page - 1) * limit
    data_result = await db.execute(
        base_q.order_by(Payment.created_at.desc()).offset(offset).limit(limit)
    )
    rows = data_result.all()

    items = [
        ProfessionalTransaction(
            consult_request_id=payment.consult_request_id,
            payment_id=payment.id,
            scheduled_at=scheduled_at,
            created_at=payment.created_at,
            amount_total=payment.amount_cents,
            platform_fee_amount=payment.platform_fee_cents,
            professional_amount=payment.professional_amount_cents,
            financial_status=_to_financial_status(payment.status),
        )
        for payment, scheduled_at in rows
    ]

    return items, total
