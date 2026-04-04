from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.models.patient_profile import PatientProfile
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import PatientProfileResponse, PatientProfileUpdate

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found")
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile
