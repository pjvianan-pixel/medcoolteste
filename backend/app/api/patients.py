import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    PatientProfileResponse,
    PatientProfileUpdate,
    QuoteRequest,
    QuoteResponse,
)
from app.services.pricing import calculate_price, quote_expires_at

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

    # Calculate price (demand=0 for MVP, no active orders table yet)
    try:
        pricing_result = await calculate_price(body.specialty_id, db, demand=0)
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
