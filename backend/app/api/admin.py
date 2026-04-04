import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    AdminProfessionalResponse,
    RejectRequest,
    SpecialtyCreate,
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
