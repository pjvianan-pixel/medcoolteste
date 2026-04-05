"""Payment gateway integration module.

Provides an abstract ``PaymentGatewayClient`` interface and a concrete
``PagarmeClient`` implementation for the Pagar.me v5 API.

Design goals
------------
- Decoupled: swap the gateway by providing a different ``PaymentGatewayClient``
  subclass without touching domain or service code.
- No secrets in source: all credentials come from ``app.core.config.settings``.
- Graceful degradation: if ``PAGARME_API_KEY`` is not configured the client
  raises ``GatewayNotConfiguredError`` so callers can handle the missing
  integration without crashing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.db.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)


class GatewayNotConfiguredError(RuntimeError):
    """Raised when required gateway credentials are not set."""


# ── Data Transfer Objects ─────────────────────────────────────────────────────


@dataclass
class PaymentGatewayChargeResponse:
    """Response returned by ``PaymentGatewayClient.create_charge``."""

    gateway_payment_id: str
    status: str
    checkout_url: str | None = None
    gateway_charge_id: str | None = None


@dataclass
class PaymentGatewayRefundResponse:
    """Response returned by ``PaymentGatewayClient.create_refund``."""

    gateway_refund_id: str
    status: str
    amount_cents: int


@dataclass
class PaymentGatewayWebhookEvent:
    """Parsed webhook event from a payment gateway."""

    gateway_payment_id: str
    gateway_event_id: str
    new_status: PaymentStatus
    raw_payload: dict = field(default_factory=dict)


# ── Abstract interface ────────────────────────────────────────────────────────


class PaymentGatewayClient(ABC):
    """Abstract payment gateway client.

    Subclasses must implement ``create_charge``, ``parse_webhook``, and
    ``create_refund``.
    """

    @abstractmethod
    async def create_charge(
        self,
        payment: Payment,
        recipient_id: str | None = None,
    ) -> PaymentGatewayChargeResponse:
        """Create a charge/order at the gateway and return gateway identifiers.

        Args:
            payment: The domain ``Payment`` object with split amounts already
                     computed (``platform_fee_cents`` / ``professional_amount_cents``).
            recipient_id: The gateway-side recipient ID for the professional.
                          If ``None`` only the platform split rule is applied.

        Returns:
            A ``PaymentGatewayChargeResponse`` with the gateway order ID and an
            optional checkout/PIX URL.
        """

    @abstractmethod
    def parse_webhook(
        self,
        payload: dict,
        headers: dict,
        raw_body: bytes,
    ) -> PaymentGatewayWebhookEvent:
        """Validate and parse an inbound webhook from the gateway.

        Args:
            payload: Decoded JSON body of the webhook.
            headers: HTTP headers from the webhook request (lowercase keys).
            raw_body: Raw bytes of the request body, used for signature
                      verification.

        Returns:
            A ``PaymentGatewayWebhookEvent`` with the mapped domain status.

        Raises:
            ValueError: If the signature is invalid.
        """

    @abstractmethod
    async def create_refund(
        self,
        payment: Payment,
        amount: int | None = None,
    ) -> PaymentGatewayRefundResponse:
        """Issue a refund for a previously captured charge.

        Args:
            payment: The domain ``Payment`` with a valid ``provider_charge_id``.
            amount: Amount in cents to refund.  If ``None`` the full charge
                    amount is refunded.

        Returns:
            A ``PaymentGatewayRefundResponse`` with the gateway refund ID.

        Raises:
            GatewayNotConfiguredError: If ``PAGARME_API_KEY`` is not set.
            ValueError: If ``payment.provider_charge_id`` is missing.
            httpx.HTTPStatusError: On non-2xx responses from the gateway.
        """


# ── Pagar.me v5 implementation ────────────────────────────────────────────────

# Maps Pagar.me order/charge statuses to internal PaymentStatus values.
_PAGARME_STATUS_MAP: dict[str, PaymentStatus] = {
    "paid": PaymentStatus.paid,
    "failed": PaymentStatus.failed,
    "canceled": PaymentStatus.canceled,
    "voided": PaymentStatus.canceled,
    "processing": PaymentStatus.processing,
    "pending": PaymentStatus.pending,
    "waiting_for_risk_analysis": PaymentStatus.processing,
}

# Maps Pagar.me webhook event types to domain PaymentStatus values so that the
# event type alone (without inspecting ``data.status``) can be used to drive
# the status transition.
_PAGARME_EVENT_TYPE_STATUS_MAP: dict[str, PaymentStatus] = {
    "order.paid": PaymentStatus.paid,
    "order.payment_failed": PaymentStatus.failed,
    "order.canceled": PaymentStatus.canceled,
    "charge.paid": PaymentStatus.paid,
    "charge.payment_failed": PaymentStatus.failed,
    "charge.refunded": PaymentStatus.refunded,
    "charge.processing": PaymentStatus.processing,
    "charge.underpaid": PaymentStatus.failed,
    "charge.overpaid": PaymentStatus.paid,
}


class PagarmeClient(PaymentGatewayClient):
    """Pagar.me v5 payment gateway client.

    Credentials are read from ``app.core.config.settings``:
    - ``PAGARME_API_KEY`` – secret key for Basic authentication.
    - ``PAGARME_BASE_URL`` – base URL (default: https://api.pagar.me/core/v5).
    - ``PAGARME_WEBHOOK_SECRET`` – used to verify ``x-pagarme-signature``.
    - ``PAGARME_PLATFORM_RECIPIENT_ID`` – gateway recipient ID for the platform.
    """

    def __init__(self) -> None:
        self._api_key = settings.PAGARME_API_KEY
        self._base_url = settings.PAGARME_BASE_URL.rstrip("/")
        self._webhook_secret = settings.PAGARME_WEBHOOK_SECRET
        self._platform_recipient_id = settings.PAGARME_PLATFORM_RECIPIENT_ID

    # ── Public methods ────────────────────────────────────────────────────────

    async def create_charge(
        self,
        payment: Payment,
        recipient_id: str | None = None,
    ) -> PaymentGatewayChargeResponse:
        """Create a Pagar.me order with PIX payment method and split rules.

        The split is built from the pre-calculated ``platform_fee_cents`` and
        ``professional_amount_cents`` already stored on ``payment``.

        Args:
            payment: Domain payment object.
            recipient_id: Pagar.me recipient ID for the professional.

        Returns:
            ``PaymentGatewayChargeResponse`` with order ID and PIX QR code URL.

        Raises:
            GatewayNotConfiguredError: If ``PAGARME_API_KEY`` is not set.
            httpx.HTTPStatusError: On non-2xx responses from Pagar.me.
        """
        if not self._api_key:
            raise GatewayNotConfiguredError(
                "PAGARME_API_KEY is not configured. "
                "Set it in your environment or .env file."
            )

        body = self._build_order_payload(payment, recipient_id)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            auth=(self._api_key, ""),
            timeout=30.0,
        ) as client:
            response = await client.post("/orders", json=body)
            response.raise_for_status()

        data: dict = response.json()
        checkout_url = self._extract_checkout_url(data)
        gateway_charge_id = self._extract_charge_id(data)

        logger.info(
            "Pagar.me order created",
            extra={"order_id": data.get("id"), "payment_id": str(payment.id)},
        )
        return PaymentGatewayChargeResponse(
            gateway_payment_id=data["id"],
            status=data.get("status", "pending"),
            checkout_url=checkout_url,
            gateway_charge_id=gateway_charge_id,
        )

    def parse_webhook(
        self,
        payload: dict,
        headers: dict,
        raw_body: bytes,
    ) -> PaymentGatewayWebhookEvent:
        """Validate the Pagar.me webhook signature and parse the event.

        Pagar.me sends:
        - Header ``x-pagarme-signature``: ``t=<ts>,v1=<hmac-sha256-hex>``
        - Body: JSON object with ``id`` (event ID), ``type``, and ``data``.

        Args:
            payload: Decoded JSON body.
            headers: HTTP headers (lowercase keys expected).
            raw_body: Raw request body bytes for signature verification.

        Returns:
            ``PaymentGatewayWebhookEvent`` mapped to a domain ``PaymentStatus``.

        Raises:
            ValueError: If the signature header is invalid or the HMAC does
                        not match.
        """
        if self._webhook_secret:
            self._verify_signature(headers, raw_body)

        event_id: str = payload.get("id", "")
        event_type: str = payload.get("type", "")
        data: dict = payload.get("data", {})

        # Prefer event-type-based status mapping; fall back to data.status.
        new_status = _PAGARME_EVENT_TYPE_STATUS_MAP.get(event_type) or _PAGARME_STATUS_MAP.get(
            data.get("status", ""), PaymentStatus.pending
        )

        gateway_payment_id = data.get("id", "")

        return PaymentGatewayWebhookEvent(
            gateway_payment_id=gateway_payment_id,
            gateway_event_id=event_id,
            new_status=new_status,
            raw_payload=payload,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_order_payload(
        self,
        payment: Payment,
        recipient_id: str | None,
    ) -> dict:
        """Build the Pagar.me v5 order request payload."""
        split_rules = []

        if self._platform_recipient_id:
            split_rules.append(
                {
                    "recipient_id": self._platform_recipient_id,
                    "amount": payment.platform_fee_cents,
                    "type": "flat",
                    "options": {"charge_processing_fee": True, "liable": True},
                }
            )

        if recipient_id:
            split_rules.append(
                {
                    "recipient_id": recipient_id,
                    "amount": payment.professional_amount_cents,
                    "type": "flat",
                    "options": {"charge_processing_fee": False, "liable": False},
                }
            )

        payment_entry: dict = {
            "payment_method": "pix",
            "pix": {"expires_in": 3600},
        }
        if split_rules:
            payment_entry["split"] = split_rules

        return {
            "code": str(payment.id),
            "currency": payment.currency,
            "items": [
                {
                    "amount": payment.amount_cents,
                    "description": "Consulta médica",
                    "quantity": 1,
                    "code": str(payment.consult_request_id),
                }
            ],
            "payments": [payment_entry],
        }

    def _extract_checkout_url(self, data: dict) -> str | None:
        """Extract the PIX QR code URL from a Pagar.me order response."""
        charges: list = data.get("charges", [])
        if not charges:
            return None
        last_transaction: dict = charges[0].get("last_transaction", {})
        return last_transaction.get("qr_code_url") or last_transaction.get("url")

    def _extract_charge_id(self, data: dict) -> str | None:
        """Extract the first charge ID from a Pagar.me order response."""
        charges: list = data.get("charges", [])
        if not charges:
            return None
        return charges[0].get("id")

    async def create_refund(
        self,
        payment: Payment,
        amount: int | None = None,
    ) -> PaymentGatewayRefundResponse:
        """Issue a refund for a Pagar.me charge.

        Calls ``POST /charges/{charge_id}/refund`` on the Pagar.me v5 API.

        Args:
            payment: Domain payment with a valid ``provider_charge_id``.
            amount: Amount in cents to refund.  ``None`` refunds the full charge.

        Returns:
            ``PaymentGatewayRefundResponse`` with the refund ID and amount.

        Raises:
            GatewayNotConfiguredError: If ``PAGARME_API_KEY`` is not set.
            ValueError: If ``payment.provider_charge_id`` is missing.
            httpx.HTTPStatusError: On non-2xx responses from Pagar.me.
        """
        if not self._api_key:
            raise GatewayNotConfiguredError(
                "PAGARME_API_KEY is not configured. "
                "Set it in your environment or .env file."
            )

        charge_id = payment.provider_charge_id
        if not charge_id:
            raise ValueError(
                f"Payment {payment.id} has no provider_charge_id; cannot refund."
            )

        body: dict = {}
        if amount is not None:
            body["amount"] = amount

        async with httpx.AsyncClient(
            base_url=self._base_url,
            auth=(self._api_key, ""),
            timeout=30.0,
        ) as client:
            response = await client.post(f"/charges/{charge_id}/refund", json=body)
            response.raise_for_status()

        data: dict = response.json()
        refunds: list = data.get("refunds", [])
        if refunds:
            refund = refunds[-1]
            gateway_refund_id: str = refund.get("id", charge_id)
            refund_amount: int = refund.get("amount", amount or payment.amount_cents)
        else:
            gateway_refund_id = charge_id
            refund_amount = amount if amount is not None else payment.amount_cents

        logger.info(
            "Pagar.me refund created",
            extra={
                "charge_id": charge_id,
                "refund_id": gateway_refund_id,
                "payment_id": str(payment.id),
            },
        )
        return PaymentGatewayRefundResponse(
            gateway_refund_id=gateway_refund_id,
            status=data.get("status", "refunded"),
            amount_cents=refund_amount,
        )

    def _verify_signature(self, headers: dict, raw_body: bytes) -> None:
        """Verify the Pagar.me webhook HMAC-SHA256 signature.

        Expected header format: ``t=<timestamp>,v1=<hex-digest>``

        Raises:
            ValueError: If the signature is missing or invalid.
        """
        sig_header = headers.get("x-pagarme-signature", "")
        if not sig_header:
            raise ValueError("Missing x-pagarme-signature header")

        parts = {k: v for k, v in (p.split("=", 1) for p in sig_header.split(",") if "=" in p)}
        timestamp = parts.get("t", "")
        v1_sig = parts.get("v1", "")

        if not timestamp or not v1_sig:
            raise ValueError("Malformed x-pagarme-signature header")

        signed_payload = f"{timestamp}.{raw_body.decode('utf-8', errors='replace')}".encode()
        expected = hmac.new(
            self._webhook_secret.encode(),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(v1_sig, expected):
            raise ValueError("Webhook signature verification failed")
