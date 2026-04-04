from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.models.professional_profile import ProfessionalProfile
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import ProfessionalProfileResponse, ProfessionalProfileUpdate

router = APIRouter(prefix="/professionals", tags=["professionals"])

_professional_dep = require_role(UserRole.professional)


@router.get("/me", response_model=ProfessionalProfileResponse)
async def get_professional_me(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalProfile:
    result = await db.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Professional profile not found"
        )
    return profile


@router.patch("/me", response_model=ProfessionalProfileResponse)
async def patch_professional_me(
    body: ProfessionalProfileUpdate,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalProfile:
    result = await db.execute(
        select(ProfessionalProfile).where(ProfessionalProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Professional profile not found"
        )

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile
