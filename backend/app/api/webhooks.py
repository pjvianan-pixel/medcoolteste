"""Webhook endpoints for external payment gateway callbacks.

Currently handles Pagar.me v5 order/charge status update webhooks.

Security
--------
- Signature verification is performed via HMAC-SHA256 when
  ``PAGARME_WEBHOOK_SECRET`` is set.
- Each webhook carries a unique ``id`` (``gateway_event_id``) that is stored
  on the ``PaymentEvent`` with a UNIQUE constraint, making the handler
  idempotent: duplicate deliveries are silently acknowledged.

Flow
----
1. Receive POST /webhooks/payments/pagarme.
2. Read raw body for signature verification *before* JSON parsing.
3. Call ``PagarmeClient.parse_webhook`` – raises ``ValueError`` on bad sig.
4. Look up the ``Payment`` by ``provider_payment_id`` (gateway order ID).
5. Idempotency check: if ``gateway_event_id`` already exists in
   ``payment_events``, return 200 immediately.
6. Create a ``PaymentEvent`` of type ``provider_callback``.
7. Update ``Payment.status`` via ``update_payment_status``.
8. Commit and return ``{"status": "ok"}``.
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.payment import Payment, PaymentEvent, PaymentEventType
from app.db.session import get_db
from app.integrations.pagarme_client import PagarmeClient
from app.services.payments import update_payment_status

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payments/pagarme", status_code=status.HTTP_200_OK)
async def pagarme_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Receive and process a Pagar.me payment status webhook.

    Returns ``{"status": "ok"}`` on success or ``{"status": "ignored"}`` when
    the event has already been processed (idempotent re-delivery).

    Raises:
        HTTPException 400: If the webhook signature is invalid.
    """
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    headers = {k.lower(): v for k, v in request.headers.items()}

    client = PagarmeClient()
    try:
        event = client.parse_webhook(payload, headers, raw_body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Locate the payment by gateway order ID.
    result = await db.execute(
        select(Payment).where(Payment.provider_payment_id == event.gateway_payment_id)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        # Unknown payment – acknowledge to stop retries.
        return {"status": "ignored", "reason": "payment not found"}

    # Idempotency: check if this gateway_event_id was already processed.
    if event.gateway_event_id:
        dup_result = await db.execute(
            select(PaymentEvent).where(
                PaymentEvent.gateway_event_id == event.gateway_event_id
            )
        )
        if dup_result.scalar_one_or_none() is not None:
            return {"status": "ignored", "reason": "already processed"}

    # Record the provider_callback event.
    pe = PaymentEvent(
        id=uuid.uuid4(),
        payment_id=payment.id,
        event_type=PaymentEventType.provider_callback,
        gateway_event_id=event.gateway_event_id or None,
        raw_payload=json.dumps(event.raw_payload),
    )
    db.add(pe)

    # Transition payment to the new status.
    await update_payment_status(payment, event.new_status, db)

    try:
        await db.commit()
    except IntegrityError:
        # Race condition: another request processed the same event_id.
        await db.rollback()
        return {"status": "ignored", "reason": "already processed"}

    return {"status": "ok"}
