"""Payment domain service: create payments and update status.

This module contains business logic for the internal payment domain.
It does not integrate with any external payment gateway; that is reserved
for F4 Part 2.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.consult_request import ConsultRequest
from app.db.models.payment import Payment, PaymentEvent, PaymentEventType, PaymentStatus


async def create_payment_for_consult_request(
    consult_request: ConsultRequest,
    db: AsyncSession,
) -> Payment:
    """Create a pending Payment for a matched consult request.

    Calculates the split between the platform and the professional using
    ``settings.PLATFORM_FEE_PERCENT``.  The ``consult_request`` must already
    have its ``quote`` relationship loaded (or it will fail).

    Args:
        consult_request: A matched ConsultRequest with an eagerly-loaded quote.
        db: The current async database session.

    Returns:
        The newly created Payment (not yet committed).
    """
    amount_cents: int = consult_request.quote.quoted_price_cents
    platform_fee_cents: int = round(amount_cents * settings.PLATFORM_FEE_PERCENT / 100)
    professional_amount_cents: int = amount_cents - platform_fee_cents

    payment = Payment(
        id=uuid.uuid4(),
        consult_request_id=consult_request.id,
        patient_user_id=consult_request.patient_user_id,
        professional_user_id=consult_request.matched_professional_user_id,
        amount_cents=amount_cents,
        currency="BRL",
        platform_fee_cents=platform_fee_cents,
        professional_amount_cents=professional_amount_cents,
        provider="pending",
        status=PaymentStatus.pending,
    )
    db.add(payment)
    await db.flush()

    event = PaymentEvent(
        id=uuid.uuid4(),
        payment_id=payment.id,
        event_type=PaymentEventType.created,
    )
    db.add(event)
    await db.flush()

    return payment


async def update_payment_status(
    payment: Payment,
    new_status: PaymentStatus,
    db: AsyncSession,
) -> Payment:
    """Transition a payment to a new status and record a status_changed event.

    Args:
        payment: The Payment to update.
        new_status: The target PaymentStatus.
        db: The current async database session.

    Returns:
        The updated Payment (not yet committed).
    """
    payment.status = new_status

    event = PaymentEvent(
        id=uuid.uuid4(),
        payment_id=payment.id,
        event_type=PaymentEventType.status_changed,
    )
    db.add(event)
    await db.flush()

    return payment
