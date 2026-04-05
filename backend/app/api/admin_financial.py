"""Admin financial endpoints (F4 Part 4B).

Provides platform-wide financial visibility and payout management for admins.

Endpoints
---------
GET  /admin/financial/summary        – aggregated platform totals
GET  /admin/financial/transactions   – paginated transaction list
POST /admin/financial/payouts        – mark payments as paid out
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.schemas import (
    AdminFinancialSummaryResponse,
    AdminFinancialTransactionItem,
    AdminFinancialTransactionsResponse,
    AdminPayoutRequest,
    AdminPayoutResult,
    AdminPayoutProfessionalSummary,
)
from app.services.admin_financials import (
    create_payouts,
    get_admin_financial_summary,
    list_admin_financial_transactions,
)
from app.services.professional_financials import FinancialStatus

router = APIRouter(prefix="/admin/financial", tags=["admin-financial"])


@router.get("/summary", response_model=AdminFinancialSummaryResponse)
async def admin_financial_summary(
    from_date: datetime | None = Query(None, description="Include payments from this datetime"),
    to_date: datetime | None = Query(None, description="Include payments up to this datetime"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminFinancialSummaryResponse:
    """Return a platform-wide financial summary (admin only)."""
    summary = await get_admin_financial_summary(db, from_date=from_date, to_date=to_date)
    return AdminFinancialSummaryResponse(
        total_payments_cents=summary.total_payments_cents,
        total_platform_fees_cents=summary.total_platform_fees_cents,
        total_professional_amount_cents=summary.total_professional_amount_cents,
        total_refunded_cents=summary.total_refunded_cents,
    )


@router.get("/transactions", response_model=AdminFinancialTransactionsResponse)
async def admin_financial_transactions(
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    financial_status: FinancialStatus | None = Query(None),
    professional_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminFinancialTransactionsResponse:
    """Return a paginated list of all platform transactions (admin only)."""
    items, total = await list_admin_financial_transactions(
        db,
        from_date=from_date,
        to_date=to_date,
        financial_status=financial_status,
        professional_user_id=professional_id,
        page=page,
        limit=limit,
    )
    return AdminFinancialTransactionsResponse(
        items=[
            AdminFinancialTransactionItem(
                payment_id=t.payment_id,
                consult_request_id=t.consult_request_id,
                patient_user_id=t.patient_user_id,
                professional_user_id=t.professional_user_id,
                amount_total_cents=t.amount_total_cents,
                platform_fee_cents=t.platform_fee_cents,
                professional_amount_cents=t.professional_amount_cents,
                financial_status=t.financial_status,
                payout_id=t.payout_id,
                created_at=t.created_at,
            )
            for t in items
        ],
        total=total,
        page=page,
        limit=limit,
    )


@router.post(
    "/payouts",
    response_model=AdminPayoutResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_payouts(
    body: AdminPayoutRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPayoutResult:
    """Mark a list of payments as paid out to their professionals (admin only).

    Payments already linked to a payout are silently skipped (idempotent).
    """
    result = await create_payouts(body.payment_ids, db)
    return AdminPayoutResult(
        payouts_created=result.payouts_created,
        payments_included=result.payments_included,
        already_paid=result.already_paid,
        professional_summaries=[
            AdminPayoutProfessionalSummary(
                professional_user_id=s.professional_user_id,
                payout_id=s.payout_id,
                total_professional_amount_cents=s.total_professional_amount_cents,
                payment_count=s.payment_count,
            )
            for s in result.professional_summaries
        ],
    )
