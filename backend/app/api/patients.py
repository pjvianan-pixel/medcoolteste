import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_role
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    ConsultRequestCreate,
    ConsultRequestResponse,
    PatientProfileResponse,
    PatientProfileUpdate,
    QuoteRequest,
    QuoteResponse,
)
from app.services.matching import run_matching
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

    if quote.expires_at.replace(tzinfo=UTC) < datetime.now(tz=UTC) if quote.expires_at.tzinfo is None else quote.expires_at < datetime.now(tz=UTC):
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

    # Reload with offers
    reload_result = await db.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.offers))
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
        .options(selectinload(ConsultRequest.offers))
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
        .options(selectinload(ConsultRequest.offers))
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
