"""Cancellation and no-show domain service.

Handles three scenarios:
- Patient cancels a matched consult request (with policy-based refund).
- Professional cancels a matched consult request (always full refund).
- Professional (or platform) marks a patient no-show (policy-based refund).

The cancellation policy is driven by ``CancellationPolicy``, defaulting to
values read from ``app.core.config.settings``.  Pass a custom policy in tests
or to override platform-wide settings.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.payment import Payment, PaymentEvent, PaymentEventType, PaymentStatus

if TYPE_CHECKING:
    from app.integrations.pagarme_client import PaymentGatewayClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CancellationPolicy:
    """Configurable rules for cancellation, late fees, and no-show handling."""

    min_hours_full_refund: int = 24
    """Hours before the scheduled appointment below which a fee may apply."""

    late_cancellation_fee_percent: int = 50
    """Percentage of the total amount retained by the platform on late cancel."""

    no_show_refund_percent: int = 0
    """Percentage of the total amount refunded to the patient on no-show."""

    no_show_grace_minutes: int = 15
    """Minutes after scheduled_at before a no-show can be registered."""


def get_default_policy() -> CancellationPolicy:
    """Build the default policy from application settings."""
    return CancellationPolicy(
        min_hours_full_refund=settings.CANCELLATION_MIN_HOURS_FULL_REFUND,
        late_cancellation_fee_percent=settings.CANCELLATION_LATE_FEE_PERCENT,
        no_show_refund_percent=settings.CANCELLATION_NO_SHOW_REFUND_PERCENT,
        no_show_grace_minutes=settings.CANCELLATION_NO_SHOW_GRACE_MINUTES,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _get_active_payment(
    consult_request_id: uuid.UUID,
    db: AsyncSession,
) -> Payment | None:
    """Return the most recent active payment for a consult request."""
    result = await db.execute(
        select(Payment)
        .where(Payment.consult_request_id == consult_request_id)
        .order_by(Payment.created_at.desc())
    )
    return result.scalar_one_or_none()


async def _has_pending_refund(payment: Payment, db: AsyncSession) -> bool:
    """Return True if a refund has already been requested for this payment."""
    result = await db.execute(
        select(PaymentEvent).where(
            PaymentEvent.payment_id == payment.id,
            PaymentEvent.event_type.in_(
                [PaymentEventType.refund_requested, PaymentEventType.refund_completed]
            ),
        )
    )
    return result.scalar_one_or_none() is not None


def _calc_percent_amount(total_cents: int, percent: int) -> int:
    """Return ``percent``% of ``total_cents`` rounded to nearest cent."""
    return round(total_cents * percent / 100)


async def _issue_refund(
    payment: Payment,
    refund_amount: int,
    db: AsyncSession,
    gateway_client: PaymentGatewayClient | None,
) -> None:
    """Issue a refund at the gateway and record the PaymentEvent.

    If a refund event already exists (idempotency guard), this is a no-op.
    If the gateway client is not provided or the payment has no charge ID the
    refund is recorded in the domain only (provider stays as-is).
    """
    if await _has_pending_refund(payment, db):
        logger.info(
            "Refund already recorded for payment %s; skipping duplicate.", payment.id
        )
        return

    gateway_refund_id: str | None = None

    if gateway_client is not None and payment.provider_charge_id:
        try:
            refund_resp = await gateway_client.create_refund(payment, amount=refund_amount)
            gateway_refund_id = refund_resp.gateway_refund_id
        except Exception:
            logger.exception(
                "Gateway refund failed for payment %s; recording domain event only.",
                payment.id,
            )

    payment.status = PaymentStatus.refund_pending

    event = PaymentEvent(
        id=uuid.uuid4(),
        payment_id=payment.id,
        event_type=PaymentEventType.refund_requested,
        gateway_event_id=gateway_refund_id,
        raw_payload=json.dumps({"refund_amount_cents": refund_amount}),
    )
    db.add(event)
    await db.flush()


# ── Public service functions ──────────────────────────────────────────────────


async def cancel_by_patient(
    consult_request: ConsultRequest,
    db: AsyncSession,
    *,
    gateway_client: PaymentGatewayClient | None = None,
    policy: CancellationPolicy | None = None,
    now: datetime | None = None,
) -> ConsultRequest:
    """Cancel a matched consult request on behalf of the patient.

    Determines whether the patient is entitled to a full refund, a partial
    refund (late cancellation fee), or no refund based on ``policy`` and
    ``consult_request.scheduled_at``.

    Args:
        consult_request: A *matched* ConsultRequest owned by the patient.
        db: The current async session.
        gateway_client: Optional gateway client for issuing the refund.
        policy: Cancellation policy to apply; defaults to platform settings.
        now: Override for "current time" (useful in tests).

    Returns:
        The updated ConsultRequest.

    Raises:
        ValueError: If the request is not in a cancellable state.
    """
    if consult_request.status not in (
        ConsultRequestStatus.matched,
    ):
        raise ValueError(
            f"ConsultRequest {consult_request.id} cannot be cancelled by patient "
            f"from status '{consult_request.status}'."
        )

    if policy is None:
        policy = get_default_policy()
    if now is None:
        now = datetime.now(tz=UTC)

    payment = await _get_active_payment(consult_request.id, db)

    # Determine refund amount
    refund_amount: int = 0
    if payment is not None and payment.status in (
        PaymentStatus.pending,
        PaymentStatus.processing,
        PaymentStatus.paid,
    ):
        scheduled_at = consult_request.scheduled_at
        if scheduled_at is None:
            # No scheduled time set → treat as full refund (patient-friendly)
            refund_amount = payment.amount_cents
        else:
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=UTC)
            time_until_scheduled = scheduled_at - now
            min_hours_delta = timedelta(hours=policy.min_hours_full_refund)
            if time_until_scheduled >= min_hours_delta:
                refund_amount = payment.amount_cents
            else:
                # Late cancellation: retain fee_percent, refund the rest
                retained = _calc_percent_amount(
                    payment.amount_cents, policy.late_cancellation_fee_percent
                )
                refund_amount = payment.amount_cents - retained

    # Update consult request
    consult_request.status = ConsultRequestStatus.cancelled_by_patient
    consult_request.cancelled_at = now

    # Handle payment
    if payment is not None:
        if payment.status in (
            PaymentStatus.canceled,
            PaymentStatus.refunded,
            PaymentStatus.refund_pending,
        ):
            pass  # Already handled; idempotent
        elif payment.status == PaymentStatus.pending and not payment.provider_charge_id:
            # Not yet captured by the gateway → simple cancellation
            payment.status = PaymentStatus.canceled
            db.add(
                PaymentEvent(
                    id=uuid.uuid4(),
                    payment_id=payment.id,
                    event_type=PaymentEventType.status_changed,
                    raw_payload=json.dumps(
                        {"reason": "cancelled_by_patient", "refund_amount_cents": 0}
                    ),
                )
            )
            await db.flush()
        elif refund_amount > 0:
            await _issue_refund(payment, refund_amount, db, gateway_client)
        else:
            # No refund: just mark payment status if it was pending without capture
            db.add(
                PaymentEvent(
                    id=uuid.uuid4(),
                    payment_id=payment.id,
                    event_type=PaymentEventType.status_changed,
                    raw_payload=json.dumps(
                        {"reason": "cancelled_by_patient_no_refund"}
                    ),
                )
            )
            await db.flush()

    await db.flush()
    return consult_request


async def cancel_by_professional(
    consult_request: ConsultRequest,
    db: AsyncSession,
    *,
    gateway_client: PaymentGatewayClient | None = None,
    now: datetime | None = None,
) -> ConsultRequest:
    """Cancel a matched consult request on behalf of the professional.

    Always issues a full refund to the patient.

    Args:
        consult_request: A *matched* ConsultRequest.
        db: The current async session.
        gateway_client: Optional gateway client for issuing the refund.
        now: Override for "current time" (useful in tests).

    Returns:
        The updated ConsultRequest.

    Raises:
        ValueError: If the request is not in a cancellable state.
    """
    if consult_request.status not in (
        ConsultRequestStatus.matched,
    ):
        raise ValueError(
            f"ConsultRequest {consult_request.id} cannot be cancelled by professional "
            f"from status '{consult_request.status}'."
        )

    if now is None:
        now = datetime.now(tz=UTC)

    payment = await _get_active_payment(consult_request.id, db)

    consult_request.status = ConsultRequestStatus.cancelled_by_professional
    consult_request.cancelled_at = now

    if payment is not None:
        if payment.status in (
            PaymentStatus.canceled,
            PaymentStatus.refunded,
            PaymentStatus.refund_pending,
        ):
            pass  # Already handled; idempotent
        elif payment.status == PaymentStatus.pending and not payment.provider_charge_id:
            payment.status = PaymentStatus.canceled
            db.add(
                PaymentEvent(
                    id=uuid.uuid4(),
                    payment_id=payment.id,
                    event_type=PaymentEventType.status_changed,
                    raw_payload=json.dumps(
                        {"reason": "cancelled_by_professional", "refund_amount_cents": 0}
                    ),
                )
            )
            await db.flush()
        else:
            await _issue_refund(payment, payment.amount_cents, db, gateway_client)

    await db.flush()
    return consult_request


async def mark_no_show(
    consult_request: ConsultRequest,
    db: AsyncSession,
    *,
    gateway_client: PaymentGatewayClient | None = None,
    policy: CancellationPolicy | None = None,
    now: datetime | None = None,
) -> ConsultRequest:
    """Mark a consult request as patient no-show and apply the refund policy.

    Args:
        consult_request: A *matched* ConsultRequest.
        db: The current async session.
        gateway_client: Optional gateway client for partial refunds.
        policy: Cancellation policy to apply; defaults to platform settings.
        now: Override for "current time" (useful in tests).

    Returns:
        The updated ConsultRequest.

    Raises:
        ValueError: If the request cannot be marked as no-show (wrong status or
                    grace period has not elapsed).
    """
    if consult_request.status != ConsultRequestStatus.matched:
        raise ValueError(
            f"ConsultRequest {consult_request.id} cannot be marked as no-show "
            f"from status '{consult_request.status}'."
        )

    if policy is None:
        policy = get_default_policy()
    if now is None:
        now = datetime.now(tz=UTC)

    # Validate grace period
    if consult_request.scheduled_at is not None:
        scheduled_at = consult_request.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        earliest_no_show = scheduled_at + timedelta(minutes=policy.no_show_grace_minutes)
        if now < earliest_no_show:
            raise ValueError(
                f"No-show cannot be registered before "
                f"{earliest_no_show.isoformat()} "
                f"(grace period of {policy.no_show_grace_minutes} minutes)."
            )

    payment = await _get_active_payment(consult_request.id, db)

    consult_request.status = ConsultRequestStatus.no_show_patient
    consult_request.no_show_marked_at = now

    if payment is not None:
        refund_amount = _calc_percent_amount(
            payment.amount_cents, policy.no_show_refund_percent
        )

        db.add(
            PaymentEvent(
                id=uuid.uuid4(),
                payment_id=payment.id,
                event_type=PaymentEventType.status_changed,
                raw_payload=json.dumps(
                    {
                        "reason": "no_show_patient",
                        "refund_percent": policy.no_show_refund_percent,
                        "refund_amount_cents": refund_amount,
                    }
                ),
            )
        )
        await db.flush()

        if refund_amount > 0 and payment.status in (
            PaymentStatus.paid,
            PaymentStatus.processing,
        ):
            await _issue_refund(payment, refund_amount, db, gateway_client)

    await db.flush()
    return consult_request
