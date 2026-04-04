import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.specialty import Specialty
from app.db.models.specialty_pricing import SpecialtyPricing
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    AdminProfessionalResponse,
    RejectRequest,
    SpecialtyCreate,
    SpecialtyPricingResponse,
    SpecialtyPricingUpdate,
    SpecialtyResponse,
    SpecialtyUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/professionals", response_model=list[AdminProfessionalResponse])
async def list_professionals(
    status: VerificationStatus | None = Query(None, description="Filter by verification status"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ProfessionalProfile]:
    stmt = select(ProfessionalProfile)
    if status is not None:
        stmt = stmt.where(ProfessionalProfile.status_verificacao == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/professionals/{user_id}/approve", response_model=AdminProfessionalResponse)
async def approve_professional(
    user_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalProfile:
    profile = await _get_professional_profile(db, user_id)
    profile.status_verificacao = VerificationStatus.approved
    profile.verification_reason = None
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post("/professionals/{user_id}/reject", response_model=AdminProfessionalResponse)
async def reject_professional(
    user_id: uuid.UUID,
    body: RejectRequest,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalProfile:
    profile = await _get_professional_profile(db, user_id)
    profile.status_verificacao = VerificationStatus.rejected
    profile.verification_reason = body.reason
    await db.commit()
    await db.refresh(profile)
    return profile


async def _get_professional_profile(db: AsyncSession, user_id: uuid.UUID) -> ProfessionalProfile:
    """Fetch a professional profile by user_id, validating role and existence."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.role != UserRole.professional:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="User is not a professional",
        )
    profile_result = await db.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Professional profile not found"
        )
    return profile


# ── Specialties (admin) ───────────────────────────────────────────────────────


@router.post("/specialties", response_model=SpecialtyResponse, status_code=status.HTTP_201_CREATED)
async def create_specialty(
    body: SpecialtyCreate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Specialty:
    """Create a new specialty (admin only)."""
    existing = await db.execute(select(Specialty).where(Specialty.slug == body.slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Specialty with slug {body.slug!r} already exists",
        )
    specialty = Specialty(id=uuid.uuid4(), slug=body.slug, name=body.name, active=body.active)
    db.add(specialty)
    await db.commit()
    await db.refresh(specialty)
    return specialty


@router.patch("/specialties/{specialty_id}", response_model=SpecialtyResponse)
async def update_specialty(
    specialty_id: uuid.UUID,
    body: SpecialtyUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Specialty:
    """Update name and/or active flag of a specialty (admin only)."""
    result = await db.execute(select(Specialty).where(Specialty.id == specialty_id))
    specialty = result.scalar_one_or_none()
    if specialty is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specialty not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(specialty, field, value)
    await db.commit()
    await db.refresh(specialty)
    return specialty


# ── Specialty Pricing (admin) ─────────────────────────────────────────────────


@router.get("/specialties/{specialty_id}/pricing", response_model=SpecialtyPricingResponse)
async def get_specialty_pricing(
    specialty_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SpecialtyPricing:
    """Return pricing configuration for a specialty (admin only)."""
    await _get_specialty_or_404(db, specialty_id)
    result = await db.execute(
        select(SpecialtyPricing).where(SpecialtyPricing.specialty_id == specialty_id)
    )
    pricing = result.scalar_one_or_none()
    if pricing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pricing not configured for this specialty",
        )
    return pricing


@router.put("/specialties/{specialty_id}/pricing", response_model=SpecialtyPricingResponse)
async def upsert_specialty_pricing(
    specialty_id: uuid.UUID,
    body: SpecialtyPricingUpdate,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SpecialtyPricing:
    """Create or update pricing for a specialty (admin only).

    All three fields (base, min, max) are required when creating a new record.
    When updating an existing record, only the provided fields are changed.
    """
    await _get_specialty_or_404(db, specialty_id)
    result = await db.execute(
        select(SpecialtyPricing).where(SpecialtyPricing.specialty_id == specialty_id)
    )
    pricing = result.scalar_one_or_none()

    if pricing is None:
        # Creating new: all fields required
        update_data = body.model_dump(exclude_unset=True)
        missing = [
            f for f in ("base_price_cents", "min_price_cents", "max_price_cents")
            if f not in update_data
        ]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Fields required for new pricing record: {missing}",
            )
        pricing = SpecialtyPricing(
            id=uuid.uuid4(),
            specialty_id=specialty_id,
            **update_data,
        )
        db.add(pricing)
    else:
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(pricing, field, value)

    await db.commit()
    await db.refresh(pricing)
    return pricing


async def _get_specialty_or_404(db: AsyncSession, specialty_id: uuid.UUID) -> Specialty:
    result = await db.execute(select(Specialty).where(Specialty.id == specialty_id))
    specialty = result.scalar_one_or_none()
    if specialty is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Specialty not found")
    return specialty
