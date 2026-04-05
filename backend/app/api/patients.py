import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_role
from app.db.models.consult_offer import (
    ActorRole,
    ConsultOffer,
    ConsultOfferEvent,
    ConsultOfferStatus,
    CounterStatus,
    EventType,
)
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    ConsultOfferResponse,
    ConsultRequestCreate,
    ConsultRequestResponse,
    PatientProfileResponse,
    PatientProfileUpdate,
    PaymentResponse,
    QuoteRequest,
    QuoteResponse,
)
from app.services.matching import run_matching
from app.services.payments import create_payment_for_consult_request
from app.services.pricing import calculate_price, get_demand_for_specialty, quote_expires_at

router = APIRouter(prefix="/patients", tags=["patients"])

_patient_dep = require_role(UserRole.patient)


@router.get("/me", response_model=PatientProfileResponse)
async def get_patient_me(
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> PatientProfile:
    result = await db.execute(
        select(PatientProfile).where(PatientProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found"
        )
    return profile


@router.patch("/me", response_model=PatientProfileResponse)
async def patch_patient_me(
    body: PatientProfileUpdate,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> PatientProfile:
    result = await db.execute(
        select(PatientProfile).where(PatientProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found"
        )

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post("/me/quotes", response_model=QuoteResponse, status_code=status.HTTP_201_CREATED)
async def create_quote(
    body: QuoteRequest,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultQuote:
    """Generate a fixed-price quote for a specialty (patient only).

    The quoted price is calculated at request time using the dynamic pricing
    engine and will not change for the lifetime of the quote (5 minutes).
    """
    # Validate specialty exists and is active
    specialty_result = await db.execute(
        select(Specialty).where(Specialty.id == body.specialty_id, Specialty.active.is_(True))
    )
    specialty = specialty_result.scalar_one_or_none()
    if specialty is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialty not found or inactive",
        )

    # Calculate price using real demand from active consult requests
    try:
        demand = await get_demand_for_specialty(body.specialty_id, db)
        pricing_result = await calculate_price(body.specialty_id, db, demand=demand)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=current_user.id,
        specialty_id=body.specialty_id,
        quoted_price_cents=pricing_result.suggested_price_cents,
        currency="BRL",
        expires_at=quote_expires_at(),
        status=QuoteStatus.active,
    )
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return quote


# ── Consult Requests ──────────────────────────────────────────────────────────


@router.post(
    "/me/consult-requests",
    response_model=ConsultRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_consult_request(
    body: ConsultRequestCreate,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Create a consult request from a valid quote (patient only).

    - Validates quote is active, not expired, and belongs to the patient.
    - Creates the consult_request in status 'queued'.
    - Runs the initial matching and transitions to 'offering' if offers are created.
    - Marks the quote as 'used'.
    """
    # Load quote with eager load check
    quote_result = await db.execute(
        select(ConsultQuote).where(ConsultQuote.id == body.quote_id)
    )
    quote = quote_result.scalar_one_or_none()

    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")

    if quote.patient_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quote does not belong to this patient")

    if quote.status != QuoteStatus.active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Quote is not active",
        )

    # Ensure timezone-aware comparison (SQLite returns naive datetimes)
    expires_at = quote.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(tz=UTC):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Quote has expired",
        )

    consult_request = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=current_user.id,
        specialty_id=quote.specialty_id,
        quote_id=quote.id,
        complaint=body.complaint,
        status=ConsultRequestStatus.queued,
    )
    db.add(consult_request)

    # Mark quote as used
    quote.status = QuoteStatus.used

    await db.flush()

    # Eager-load the quote relationship needed by matching
    await db.refresh(consult_request)
    consult_request.quote = quote

    # Run initial matching
    await run_matching(consult_request, db)

    await db.commit()

    # Reload with offers and events
    reload_result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(ConsultRequest.id == consult_request.id)
    )
    return reload_result.scalar_one()


@router.get(
    "/me/consult-requests/{request_id}",
    response_model=ConsultRequestResponse,
)
async def get_consult_request(
    request_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Get a specific consult request with its status, offers, and matched professional."""
    result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.patient_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )
    return consult_request


@router.post(
    "/me/consult-requests/{request_id}/cancel",
    response_model=ConsultRequestResponse,
)
async def cancel_consult_request(
    request_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Cancel a consult request if it has not been matched yet."""
    result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.patient_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )

    if consult_request.status == ConsultRequestStatus.matched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot cancel a matched consult request",
        )

    if consult_request.status == ConsultRequestStatus.canceled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consult request is already canceled",
        )

    consult_request.status = ConsultRequestStatus.canceled
    await db.commit()
    await db.refresh(consult_request)
    return consult_request


async def _get_offer_for_patient(
    offer_id: uuid.UUID,
    patient_user_id: uuid.UUID,
    db: AsyncSession,
) -> ConsultOffer:
    """Load an offer and verify it belongs to a consult_request owned by the patient."""
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .join(ConsultRequest, ConsultRequest.id == ConsultOffer.consult_request_id)
        .where(
            ConsultOffer.id == offer_id,
            ConsultRequest.patient_user_id == patient_user_id,
        )
    )
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")
    return offer


@router.post(
    "/me/offers/{offer_id}/counter/accept",
    response_model=ConsultOfferResponse,
    status_code=status.HTTP_200_OK,
)
async def accept_counter_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Accept a professional's counter offer.

    - counter_status must be pending.
    - Marks counter_status=accepted, offer.status=accepted.
    - Marks consult_request as matched.
    - Expires all other pending offers for the same request.
    - Creates a counter_accepted event.
    """
    offer = await _get_offer_for_patient(offer_id, current_user.id, db)

    if offer.counter_status != CounterStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No pending counter offer on this offer",
        )

    now = datetime.now(tz=UTC)
    offer.counter_status = CounterStatus.accepted
    offer.counter_responded_at = now
    offer.status = ConsultOfferStatus.accepted
    offer.responded_at = now

    request_result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == offer.consult_request_id)
    )
    consult_request = request_result.scalar_one()
    consult_request.status = ConsultRequestStatus.matched
    consult_request.matched_professional_user_id = offer.professional_user_id

    # Expire other pending offers
    await db.execute(
        update(ConsultOffer)
        .where(
            ConsultOffer.consult_request_id == offer.consult_request_id,
            ConsultOffer.id != offer_id,
            ConsultOffer.status == ConsultOfferStatus.pending,
        )
        .values(status=ConsultOfferStatus.expired, responded_at=now)
    )

    event = ConsultOfferEvent(
        id=uuid.uuid4(),
        consult_offer_id=offer.id,
        actor_role=ActorRole.patient,
        event_type=EventType.counter_accepted,
        price_cents=None,
        created_at=now,
    )
    db.add(event)

    await db.commit()
    # Expire cached identity so reload fetches fresh data including new event
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


@router.post(
    "/me/offers/{offer_id}/counter/reject",
    response_model=ConsultOfferResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_counter_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Reject a professional's counter offer.

    - counter_status must be pending.
    - Marks counter_status=rejected, offer.status=rejected.
    - Creates a counter_rejected event.
    - If no other pending offers remain for the consult_request, re-runs matching.
    """
    offer = await _get_offer_for_patient(offer_id, current_user.id, db)

    if offer.counter_status != CounterStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No pending counter offer on this offer",
        )

    now = datetime.now(tz=UTC)
    offer.counter_status = CounterStatus.rejected
    offer.counter_responded_at = now
    offer.status = ConsultOfferStatus.rejected
    offer.responded_at = now

    event = ConsultOfferEvent(
        id=uuid.uuid4(),
        consult_offer_id=offer.id,
        actor_role=ActorRole.patient,
        event_type=EventType.counter_rejected,
        price_cents=None,
        created_at=now,
    )
    db.add(event)

    await db.flush()

    # Check if any pending offers remain
    pending_result = await db.execute(
        select(ConsultOffer).where(
            ConsultOffer.consult_request_id == offer.consult_request_id,
            ConsultOffer.status == ConsultOfferStatus.pending,
            ConsultOffer.id != offer_id,
        )
    )
    remaining_pending = list(pending_result.scalars().all())

    if not remaining_pending:
        request_result = await db.execute(
            select(ConsultRequest).where(ConsultRequest.id == offer.consult_request_id)
        )
        consult_request = request_result.scalar_one()
        if consult_request.status != ConsultRequestStatus.matched:
            # Reload quote for matching
            from app.db.models.consult_quote import ConsultQuote  # noqa: PLC0415

            quote_result = await db.execute(
                select(ConsultQuote).where(ConsultQuote.id == consult_request.quote_id)
            )
            consult_request.quote = quote_result.scalar_one()
            await run_matching(consult_request, db)

    await db.commit()
    # Expire cached identity so reload fetches fresh data including new event
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# ── Payments ──────────────────────────────────────────────────────────────────


@router.post(
    "/me/consult-requests/{request_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment(
    request_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> Payment:
    """Create a payment for a matched consult request (patient only).

    - The consult_request must belong to the authenticated patient.
    - The consult_request must be in 'matched' status.
    - No active payment (pending/processing/paid) must already exist for it.
    """
    result = await db.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.quote))
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.patient_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )

    if consult_request.status != ConsultRequestStatus.matched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consult request is not matched yet",
        )

    # Check for existing active payment
    existing_result = await db.execute(
        select(Payment).where(
            Payment.consult_request_id == request_id,
            Payment.status.in_(
                [PaymentStatus.pending, PaymentStatus.processing, PaymentStatus.paid]
            ),
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An active payment already exists for this consult request",
        )

    payment = await create_payment_for_consult_request(consult_request, db)
    await db.commit()
    await db.refresh(payment)
    return payment


@router.get("/me/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> Payment:
    """Get details of a specific payment belonging to the authenticated patient."""
    result = await db.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.patient_user_id == current_user.id,
        )
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    return payment
