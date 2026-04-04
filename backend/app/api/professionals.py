import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    PresenceResponse,
    ProfessionalProfileResponse,
    ProfessionalProfileUpdate,
    ProfessionalSpecialtiesUpdate,
    SpecialtyResponse,
)

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


@router.get("/me/specialties", response_model=list[SpecialtyResponse])
async def get_professional_specialties(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> list[Specialty]:
    """List the specialties associated with the authenticated professional."""
    result = await db.execute(
        select(Specialty)
        .join(
            ProfessionalSpecialty,
            ProfessionalSpecialty.specialty_id == Specialty.id,
        )
        .where(ProfessionalSpecialty.professional_user_id == current_user.id)
    )
    return list(result.scalars().all())


@router.put("/me/specialties", response_model=list[SpecialtyResponse])
async def replace_professional_specialties(
    body: ProfessionalSpecialtiesUpdate,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> list[Specialty]:
    """Replace the professional's specialty list with the provided one.

    Each element of ``body.specialties`` may be either a UUID string or a slug.
    Returns 400 if any identifier does not match an existing specialty.
    """
    resolved: list[Specialty] = []
    for identifier in body.specialties:
        specialty: Specialty | None = None
        # Try UUID first
        try:
            import uuid as _uuid

            spec_uuid = _uuid.UUID(identifier)
            res = await db.execute(select(Specialty).where(Specialty.id == spec_uuid))
            specialty = res.scalar_one_or_none()
        except ValueError:
            pass

        # Fallback to slug
        if specialty is None:
            res = await db.execute(select(Specialty).where(Specialty.slug == identifier))
            specialty = res.scalar_one_or_none()

        if specialty is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Specialty not found: {identifier!r}",
            )
        resolved.append(specialty)

    # Replace all existing links
    await db.execute(
        delete(ProfessionalSpecialty).where(
            ProfessionalSpecialty.professional_user_id == current_user.id
        )
    )
    for specialty in resolved:
        db.add(
            ProfessionalSpecialty(
                professional_user_id=current_user.id,
                specialty_id=specialty.id,
            )
        )
    await db.commit()
    return resolved


async def _get_or_create_presence(
    db: AsyncSession, user_id: uuid.UUID
) -> ProfessionalPresence:
    result = await db.execute(
        select(ProfessionalPresence).where(
            ProfessionalPresence.professional_user_id == user_id
        )
    )
    presence = result.scalar_one_or_none()
    if presence is None:
        presence = ProfessionalPresence(professional_user_id=user_id)
        db.add(presence)
    return presence


@router.post("/me/online", response_model=PresenceResponse, status_code=status.HTTP_200_OK)
async def set_online(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalPresence:
    """Mark the authenticated professional as online and update last_seen_at."""
    presence = await _get_or_create_presence(db, current_user.id)
    presence.is_online = True
    presence.last_seen_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(presence)
    return presence


@router.post("/me/offline", response_model=PresenceResponse, status_code=status.HTTP_200_OK)
async def set_offline(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalPresence:
    """Mark the authenticated professional as offline."""
    presence = await _get_or_create_presence(db, current_user.id)
    presence.is_online = False
    presence.last_seen_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(presence)
    return presence


@router.post("/me/heartbeat", response_model=PresenceResponse, status_code=status.HTTP_200_OK)
async def heartbeat(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalPresence:
    """Update last_seen_at to keep the professional marked as online."""
    presence = await _get_or_create_presence(db, current_user.id)
    presence.is_online = True
    presence.last_seen_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(presence)
    return presence
