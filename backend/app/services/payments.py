"""Payment domain service: create payments and update status.

This module contains business logic for the internal payment domain.
Gateway integration with Pagar.me is delegated to
``app.integrations.pagarme_client.PagarmeClient`` and is activated only when
``PAGARME_API_KEY`` is configured.  If the key is absent the payment is still
created as ``pending`` with ``provider="pending"``, which keeps the existing
test suite working without real credentials.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.consult_request import ConsultRequest
from app.db.models.payment import Payment, PaymentEvent, PaymentEventType, PaymentStatus
from app.db.models.professional_profile import ProfessionalProfile

logger = logging.getLogger(__name__)


async def create_payment_for_consult_request(
    consult_request: ConsultRequest,
    db: AsyncSession,
    *,
    gateway_client=None,  # type: ignore[assignment]
) -> Payment:
    """Create a pending Payment for a matched consult request.

    Calculates the split between the platform and the professional using
    ``settings.PLATFORM_FEE_PERCENT``.  The ``consult_request`` must already
    have its ``quote`` relationship loaded (or it will fail).

    When ``PAGARME_API_KEY`` is configured (or a ``gateway_client`` is
    explicitly passed for testing), the function calls
    ``PagarmeClient.create_charge`` and persists the returned
    ``provider_payment_id`` and ``checkout_url`` on the payment record.

    Args:
        consult_request: A matched ConsultRequest with an eagerly-loaded quote.
        db: The current async database session.
        gateway_client: Optional override of the payment gateway client used
            for dependency-injection in tests.  Defaults to a new
            ``PagarmeClient()`` instance when ``PAGARME_API_KEY`` is set.

    Returns:
        The newly created Payment (not yet committed).
    """
    if consult_request.quote is None:
        raise ValueError(
            f"ConsultRequest {consult_request.id} has no loaded quote relationship. "
            "Ensure the quote is eagerly loaded before calling this function."
        )
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

    # ── Gateway integration ───────────────────────────────────────────────────
    # Resolve the gateway client to use.  A caller-provided client is used
    # first (useful in tests); otherwise instantiate PagarmeClient when the
    # API key is configured.
    client = gateway_client
    if client is None and settings.PAGARME_API_KEY:
        from app.integrations.pagarme_client import PagarmeClient  # noqa: PLC0415

        client = PagarmeClient()

    if client is not None:
        # Look up the professional's Pagar.me recipient ID if available.
        recipient_id: str | None = None
        if consult_request.matched_professional_user_id is not None:
            result = await db.execute(
                select(ProfessionalProfile).where(
                    ProfessionalProfile.user_id
                    == consult_request.matched_professional_user_id
                )
            )
            prof_profile = result.scalar_one_or_none()
            if prof_profile is not None:
                recipient_id = prof_profile.pagarme_recipient_id

        try:
            charge = await client.create_charge(payment, recipient_id=recipient_id)
            payment.provider = "pagarme"
            payment.provider_payment_id = charge.gateway_payment_id
            payment.provider_charge_id = charge.gateway_charge_id
            payment.checkout_url = charge.checkout_url
            await db.flush()
        except Exception:
            logger.exception(
                "Gateway charge creation failed for payment %s; "
                "leaving provider=pending",
                payment.id,
            )

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
