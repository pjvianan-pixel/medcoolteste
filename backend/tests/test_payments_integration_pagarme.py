"""Integration tests for F4 Part 2: Pagar.me gateway integration.

All Pagar.me HTTP calls are intercepted by mocks/fakes so the test suite
never makes real network requests.

Covered scenarios
-----------------
1. ``create_payment_for_consult_request`` calls the gateway client with the
   correct split amounts.
2. Gateway response (gateway_payment_id, checkout_url) is persisted on the
   Payment record.
3. Gateway failure leaves provider=pending without raising.
4. POST /webhooks/payments/pagarme transitions Payment.status and creates a
   PaymentEvent.
5. Idempotent webhook: duplicate delivery of the same gateway_event_id does
   not create a second PaymentEvent or change status again.
6. Webhook for unknown gateway_payment_id returns 200 with "ignored".
7. Webhook with invalid signature returns 400.
8. ``PagarmeClient.parse_webhook`` maps Pagar.me event types to PaymentStatus.
9. ``PagarmeClient._build_order_payload`` includes correct split rules.
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.models.consult_offer import ConsultOffer, ConsultOfferStatus
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentEvent, PaymentEventType, PaymentStatus
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole
from app.integrations.pagarme_client import (
    PaymentGatewayChargeResponse,
    PaymentGatewayWebhookEvent,
    PagarmeClient,
)
from app.services.payments import create_payment_for_consult_request

# ── Helpers ───────────────────────────────────────────────────────────────────


async def _register_and_login(client: AsyncClient, email: str, role: str) -> tuple[str, str]:
    resp = await client.post("/auth/register", json={"email": email, "password": "pw", "role": role})
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"], user_id


async def _seed_specialty(db: AsyncSession, slug: str = "clinico-geral") -> Specialty:
    spec = Specialty(id=uuid.uuid4(), slug=slug, name=slug.replace("-", " ").title(), active=True)
    db.add(spec)
    await db.commit()
    await db.refresh(spec)
    return spec


async def _seed_pricing(db: AsyncSession, specialty_id: uuid.UUID) -> SpecialtyPricing:
    pricing = SpecialtyPricing(
        id=uuid.uuid4(),
        specialty_id=specialty_id,
        base_price_cents=14990,
        min_price_cents=9990,
        max_price_cents=24990,
    )
    db.add(pricing)
    await db.commit()
    await db.refresh(pricing)
    return pricing


async def _seed_patient_profile(db: AsyncSession, user_id: uuid.UUID) -> PatientProfile:
    cpf = str(user_id.int)[:11].zfill(11)
    profile = PatientProfile(
        id=uuid.uuid4(), user_id=user_id, full_name="Test Patient", cpf=cpf
    )
    db.add(profile)
    await db.commit()
    return profile


async def _seed_professional(
    db: AsyncSession,
    email: str,
    specialty_id: uuid.UUID,
    approved: bool = True,
    online: bool = True,
    pagarme_recipient_id: str | None = None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hash_password("pw"),
        role=UserRole.professional,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    status_v = VerificationStatus.approved if approved else VerificationStatus.pending
    profile = ProfessionalProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        full_name=f"Dr {email}",
        crm=f"CRM{email[:8]}",
        specialty="general",
        status_verificacao=status_v,
        pagarme_recipient_id=pagarme_recipient_id,
    )
    db.add(profile)

    ps = ProfessionalSpecialty(professional_user_id=user.id, specialty_id=specialty_id)
    db.add(ps)

    if online:
        presence = ProfessionalPresence(
            professional_user_id=user.id,
            is_online=True,
            last_seen_at=datetime.now(tz=UTC),
        )
        db.add(presence)

    await db.commit()
    await db.refresh(user)
    return user


async def _create_matched_request(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    specialty_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    price_cents: int = 14990,
) -> tuple[ConsultRequest, ConsultQuote]:
    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=patient_user_id,
        specialty_id=specialty_id,
        quoted_price_cents=price_cents,
        currency="BRL",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status=QuoteStatus.used,
    )
    db.add(quote)
    await db.flush()

    cr = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=patient_user_id,
        specialty_id=specialty_id,
        quote_id=quote.id,
        complaint="dor de cabeça",
        status=ConsultRequestStatus.matched,
        matched_professional_user_id=professional_user_id,
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    await db.refresh(quote)
    return cr, quote


def _make_fake_client(
    gateway_payment_id: str = "or_fake123",
    status: str = "pending",
    checkout_url: str | None = "https://pix.example.com/qr",
) -> MagicMock:
    """Return a mock PaymentGatewayClient whose create_charge returns a fake response."""
    client = MagicMock()
    client.create_charge = AsyncMock(
        return_value=PaymentGatewayChargeResponse(
            gateway_payment_id=gateway_payment_id,
            status=status,
            checkout_url=checkout_url,
        )
    )
    return client


# ── Service-level tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_payment_calls_gateway_with_split_amounts(
    db_session: AsyncSession,
) -> None:
    """create_payment_for_consult_request calls gateway with correct split."""
    spec = await _seed_specialty(db_session, "pagarme-split")
    await _seed_pricing(db_session, spec.id)

    pat_user = User(
        id=uuid.uuid4(),
        email="pat@pgtest1.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(pat_user)
    prof_user = await _seed_professional(
        db_session, "prof@pgtest1.com", spec.id, pagarme_recipient_id="re_prof123"
    )
    await db_session.commit()

    cr, quote = await _create_matched_request(
        db_session, pat_user.id, spec.id, prof_user.id, price_cents=10000
    )

    fake_client = _make_fake_client(gateway_payment_id="or_test001")

    # Eagerly reload quote on cr for the service function
    from sqlalchemy.orm import selectinload  # noqa: PLC0415
    result = await db_session.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.quote))
        .where(ConsultRequest.id == cr.id)
    )
    cr_loaded = result.scalar_one()

    payment = await create_payment_for_consult_request(
        cr_loaded, db_session, gateway_client=fake_client
    )
    await db_session.commit()

    # Verify gateway was called once
    fake_client.create_charge.assert_awaited_once()
    call_kwargs = fake_client.create_charge.call_args

    paid_payment: Payment = call_kwargs.args[0]
    assert paid_payment.platform_fee_cents + paid_payment.professional_amount_cents == 10000
    assert paid_payment.platform_fee_cents == round(10000 * settings.PLATFORM_FEE_PERCENT / 100)

    # recipient_id kwarg should be the professional's pagarme_recipient_id
    assert call_kwargs.kwargs.get("recipient_id") == "re_prof123"


@pytest.mark.asyncio
async def test_gateway_response_persisted_on_payment(db_session: AsyncSession) -> None:
    """provider_payment_id and checkout_url are saved when gateway succeeds."""
    spec = await _seed_specialty(db_session, "pagarme-persist")
    await _seed_pricing(db_session, spec.id)

    pat_user = User(
        id=uuid.uuid4(),
        email="pat@pgtest2.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(pat_user)
    prof_user = await _seed_professional(db_session, "prof@pgtest2.com", spec.id)
    await db_session.commit()

    cr, _ = await _create_matched_request(db_session, pat_user.id, spec.id, prof_user.id)

    fake_client = _make_fake_client(
        gateway_payment_id="or_abc999",
        checkout_url="https://pix.example.com/qr_abc999",
    )

    from sqlalchemy.orm import selectinload  # noqa: PLC0415
    result = await db_session.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.quote))
        .where(ConsultRequest.id == cr.id)
    )
    cr_loaded = result.scalar_one()

    payment = await create_payment_for_consult_request(
        cr_loaded, db_session, gateway_client=fake_client
    )
    await db_session.commit()
    await db_session.refresh(payment)

    assert payment.provider == "pagarme"
    assert payment.provider_payment_id == "or_abc999"
    assert payment.checkout_url == "https://pix.example.com/qr_abc999"


@pytest.mark.asyncio
async def test_gateway_failure_leaves_provider_pending(db_session: AsyncSession) -> None:
    """If the gateway raises an exception the payment is still created as pending."""
    spec = await _seed_specialty(db_session, "pagarme-fail")
    await _seed_pricing(db_session, spec.id)

    pat_user = User(
        id=uuid.uuid4(),
        email="pat@pgtest3.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(pat_user)
    prof_user = await _seed_professional(db_session, "prof@pgtest3.com", spec.id)
    await db_session.commit()

    cr, _ = await _create_matched_request(db_session, pat_user.id, spec.id, prof_user.id)

    failing_client = MagicMock()
    failing_client.create_charge = AsyncMock(side_effect=RuntimeError("network error"))

    from sqlalchemy.orm import selectinload  # noqa: PLC0415
    result = await db_session.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.quote))
        .where(ConsultRequest.id == cr.id)
    )
    cr_loaded = result.scalar_one()

    payment = await create_payment_for_consult_request(
        cr_loaded, db_session, gateway_client=failing_client
    )
    await db_session.commit()
    await db_session.refresh(payment)

    assert payment.provider == "pending"
    assert payment.provider_payment_id is None
    assert payment.checkout_url is None


# ── PaymentResponse includes checkout_url ────────────────────────────────────


@pytest.mark.asyncio
async def test_create_payment_endpoint_returns_checkout_url(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """POST /payments returns checkout_url and provider_payment_id from gateway."""
    from unittest.mock import patch  # noqa: PLC0415

    spec = await _seed_specialty(db_session, "pagarme-api")
    await _seed_pricing(db_session, spec.id)
    token, patient_id = await _register_and_login(client, "pat@pgapi.com", "patient")
    patient_uuid = uuid.UUID(patient_id)
    await _seed_patient_profile(db_session, patient_uuid)
    prof = await _seed_professional(db_session, "prof@pgapi.com", spec.id)

    cr, _ = await _create_matched_request(db_session, patient_uuid, spec.id, prof.id)

    fake_charge_resp = PaymentGatewayChargeResponse(
        gateway_payment_id="or_endpoint001",
        status="pending",
        checkout_url="https://pix.example.com/endpoint001",
    )

    with patch(
        "app.integrations.pagarme_client.PagarmeClient",
        return_value=MagicMock(create_charge=AsyncMock(return_value=fake_charge_resp)),
    ), patch.object(settings, "PAGARME_API_KEY", "fake_key"):
        resp = await client.post(
            f"/patients/me/consult-requests/{cr.id}/payments",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider_payment_id"] == "or_endpoint001"
    assert data["checkout_url"] == "https://pix.example.com/endpoint001"
    assert "checkout_url" in data  # field is always present (may be null)


# ── Webhook tests ─────────────────────────────────────────────────────────────


async def _seed_payment_with_gateway_id(
    db: AsyncSession,
    patient_user_id: uuid.UUID,
    professional_user_id: uuid.UUID,
    consult_request_id: uuid.UUID,
    gateway_payment_id: str,
    price_cents: int = 10000,
) -> Payment:
    payment = Payment(
        id=uuid.uuid4(),
        consult_request_id=consult_request_id,
        patient_user_id=patient_user_id,
        professional_user_id=professional_user_id,
        amount_cents=price_cents,
        currency="BRL",
        platform_fee_cents=round(price_cents * settings.PLATFORM_FEE_PERCENT / 100),
        professional_amount_cents=price_cents - round(price_cents * settings.PLATFORM_FEE_PERCENT / 100),
        provider="pagarme",
        provider_payment_id=gateway_payment_id,
        status=PaymentStatus.pending,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


@pytest.mark.asyncio
async def test_webhook_paid_event_updates_payment_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Pagar.me order.paid webhook transitions Payment to paid and creates PaymentEvent."""
    spec = await _seed_specialty(db_session, "wh-paid")
    await _seed_pricing(db_session, spec.id)

    pat_user = User(
        id=uuid.uuid4(),
        email="pat@wh1.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(pat_user)
    prof_user = await _seed_professional(db_session, "prof@wh1.com", spec.id)
    await db_session.commit()

    cr, _ = await _create_matched_request(db_session, pat_user.id, spec.id, prof_user.id)
    payment = await _seed_payment_with_gateway_id(
        db_session, pat_user.id, prof_user.id, cr.id, "or_wh001"
    )

    payload = {
        "id": "hook_event_001",
        "type": "order.paid",
        "data": {"id": "or_wh001", "status": "paid"},
    }
    resp = await client.post("/webhooks/payments/pagarme", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.paid

    # A provider_callback event must exist
    result = await db_session.execute(
        select(PaymentEvent).where(
            PaymentEvent.payment_id == payment.id,
            PaymentEvent.event_type == PaymentEventType.provider_callback,
        )
    )
    events = list(result.scalars().all())
    assert len(events) == 1
    assert events[0].gateway_event_id == "hook_event_001"


@pytest.mark.asyncio
async def test_webhook_idempotent_duplicate_delivery(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Sending the same webhook twice does not duplicate PaymentEvents."""
    spec = await _seed_specialty(db_session, "wh-idem")
    await _seed_pricing(db_session, spec.id)

    pat_user = User(
        id=uuid.uuid4(),
        email="pat@wh2.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(pat_user)
    prof_user = await _seed_professional(db_session, "prof@wh2.com", spec.id)
    await db_session.commit()

    cr, _ = await _create_matched_request(db_session, pat_user.id, spec.id, prof_user.id)
    payment = await _seed_payment_with_gateway_id(
        db_session, pat_user.id, prof_user.id, cr.id, "or_wh002"
    )

    payload = {
        "id": "hook_event_002",
        "type": "order.paid",
        "data": {"id": "or_wh002", "status": "paid"},
    }

    # First delivery
    resp = await client.post("/webhooks/payments/pagarme", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Second delivery (duplicate)
    resp2 = await client.post("/webhooks/payments/pagarme", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"

    # Only one provider_callback event should exist
    result = await db_session.execute(
        select(PaymentEvent).where(
            PaymentEvent.payment_id == payment.id,
            PaymentEvent.event_type == PaymentEventType.provider_callback,
        )
    )
    events = list(result.scalars().all())
    assert len(events) == 1


@pytest.mark.asyncio
async def test_webhook_unknown_gateway_payment_id_is_ignored(
    client: AsyncClient,
) -> None:
    """Webhook for an unknown gateway_payment_id returns 200 with 'ignored'."""
    payload = {
        "id": "hook_unknown",
        "type": "order.paid",
        "data": {"id": "or_does_not_exist", "status": "paid"},
    }
    resp = await client.post("/webhooks/payments/pagarme", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_invalid_signature_returns_400(
    client: AsyncClient,
) -> None:
    """Webhook with wrong signature returns 400 when PAGARME_WEBHOOK_SECRET is set."""
    from unittest.mock import patch  # noqa: PLC0415

    payload = {
        "id": "hook_badsig",
        "type": "order.paid",
        "data": {"id": "or_badsig", "status": "paid"},
    }
    bad_sig_header = "t=123456,v1=badhex"

    with patch.object(settings, "PAGARME_WEBHOOK_SECRET", "real_secret"):
        resp = await client.post(
            "/webhooks/payments/pagarme",
            json=payload,
            headers={"x-pagarme-signature": bad_sig_header},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_webhook_failed_event_transitions_to_failed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """order.payment_failed webhook transitions Payment to failed."""
    spec = await _seed_specialty(db_session, "wh-failed")
    await _seed_pricing(db_session, spec.id)

    pat_user = User(
        id=uuid.uuid4(),
        email="pat@wh3.com",
        hashed_password=hash_password("pw"),
        role=UserRole.patient,
        is_active=True,
    )
    db_session.add(pat_user)
    prof_user = await _seed_professional(db_session, "prof@wh3.com", spec.id)
    await db_session.commit()

    cr, _ = await _create_matched_request(db_session, pat_user.id, spec.id, prof_user.id)
    payment = await _seed_payment_with_gateway_id(
        db_session, pat_user.id, prof_user.id, cr.id, "or_wh003"
    )

    payload = {
        "id": "hook_event_003",
        "type": "order.payment_failed",
        "data": {"id": "or_wh003", "status": "failed"},
    }
    resp = await client.post("/webhooks/payments/pagarme", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.failed


# ── PagarmeClient unit tests ──────────────────────────────────────────────────


def test_parse_webhook_maps_event_type_to_status() -> None:
    """parse_webhook maps Pagar.me event types to correct PaymentStatus."""
    client = PagarmeClient.__new__(PagarmeClient)
    client._webhook_secret = ""  # type: ignore[attr-defined]

    cases = [
        ("order.paid", PaymentStatus.paid),
        ("order.payment_failed", PaymentStatus.failed),
        ("order.canceled", PaymentStatus.canceled),
        ("charge.paid", PaymentStatus.paid),
        ("charge.refunded", PaymentStatus.refunded),
        ("charge.processing", PaymentStatus.processing),
    ]
    for event_type, expected_status in cases:
        payload = {
            "id": f"hook_{event_type}",
            "type": event_type,
            "data": {"id": "or_test", "status": "unknown"},
        }
        event = client.parse_webhook(payload, {}, b"")
        assert event.new_status == expected_status, (
            f"{event_type} should map to {expected_status}, got {event.new_status}"
        )


def test_parse_webhook_falls_back_to_data_status() -> None:
    """parse_webhook falls back to data.status when event type is not in map."""
    client = PagarmeClient.__new__(PagarmeClient)
    client._webhook_secret = ""  # type: ignore[attr-defined]

    payload = {
        "id": "hook_custom",
        "type": "some.custom.event",
        "data": {"id": "or_custom", "status": "paid"},
    }
    event = client.parse_webhook(payload, {}, b"")
    assert event.new_status == PaymentStatus.paid


def test_build_order_payload_includes_split_rules() -> None:
    """_build_order_payload includes platform and professional split rules."""
    client = PagarmeClient.__new__(PagarmeClient)
    client._api_key = "test_key"  # type: ignore[attr-defined]
    client._base_url = "https://api.pagar.me/core/v5"  # type: ignore[attr-defined]
    client._webhook_secret = ""  # type: ignore[attr-defined]
    client._platform_recipient_id = "re_platform123"  # type: ignore[attr-defined]

    payment = MagicMock()
    payment.id = uuid.uuid4()
    payment.consult_request_id = uuid.uuid4()
    payment.amount_cents = 10000
    payment.currency = "BRL"
    payment.platform_fee_cents = 2000
    payment.professional_amount_cents = 8000

    payload = client._build_order_payload(payment, recipient_id="re_prof456")

    payments_list = payload["payments"]
    assert len(payments_list) == 1
    split = payments_list[0]["split"]
    assert len(split) == 2

    platform_rule = next(r for r in split if r["recipient_id"] == "re_platform123")
    assert platform_rule["amount"] == 2000

    prof_rule = next(r for r in split if r["recipient_id"] == "re_prof456")
    assert prof_rule["amount"] == 8000


def test_build_order_payload_without_professional_recipient() -> None:
    """Without a professional recipient_id only the platform split rule is added."""
    client = PagarmeClient.__new__(PagarmeClient)
    client._api_key = "test_key"  # type: ignore[attr-defined]
    client._base_url = "https://api.pagar.me/core/v5"  # type: ignore[attr-defined]
    client._webhook_secret = ""  # type: ignore[attr-defined]
    client._platform_recipient_id = "re_platform123"  # type: ignore[attr-defined]

    payment = MagicMock()
    payment.id = uuid.uuid4()
    payment.consult_request_id = uuid.uuid4()
    payment.amount_cents = 10000
    payment.currency = "BRL"
    payment.platform_fee_cents = 2000
    payment.professional_amount_cents = 8000

    payload = client._build_order_payload(payment, recipient_id=None)
    split = payload["payments"][0]["split"]
    assert len(split) == 1
    assert split[0]["recipient_id"] == "re_platform123"


def test_verify_signature_valid() -> None:
    """_verify_signature accepts a correct HMAC-SHA256 signature."""
    secret = "my_webhook_secret"
    timestamp = "1700000000"
    raw_body = b'{"id":"hook_1"}'
    signed_payload = f"{timestamp}.{raw_body.decode()}".encode()
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    client = PagarmeClient.__new__(PagarmeClient)
    client._webhook_secret = secret  # type: ignore[attr-defined]

    headers = {"x-pagarme-signature": f"t={timestamp},v1={sig}"}
    # Should not raise
    client._verify_signature(headers, raw_body)


def test_verify_signature_invalid_raises() -> None:
    """_verify_signature raises ValueError for a wrong signature."""
    client = PagarmeClient.__new__(PagarmeClient)
    client._webhook_secret = "real_secret"  # type: ignore[attr-defined]

    headers = {"x-pagarme-signature": "t=123,v1=wrongsig"}
    with pytest.raises(ValueError, match="signature"):
        client._verify_signature(headers, b'{"id":"hook_bad"}')
