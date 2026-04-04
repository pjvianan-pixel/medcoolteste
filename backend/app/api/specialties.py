from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.schemas import (
    SpecialtyAvailabilityItem,
    SpecialtyAvailabilityResponse,
    SpecialtyResponse,
)

router = APIRouter(prefix="/specialties", tags=["specialties"])


@router.get("", response_model=list[SpecialtyResponse])
async def list_specialties(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Specialty]:
    """Return all active specialties (any authenticated user)."""
    result = await db.execute(select(Specialty).where(Specialty.active.is_(True)))
    return list(result.scalars().all())


@router.get("/availability", response_model=SpecialtyAvailabilityResponse)
async def get_specialties_availability(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SpecialtyAvailabilityResponse:
    """Return the number of online professionals per active specialty.

    A professional is considered online if ``is_online`` is True and
    ``last_seen_at`` is within the last ``PRESENCE_TIMEOUT_SECONDS`` seconds.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.PRESENCE_TIMEOUT_SECONDS)

    result = await db.execute(
        select(
            Specialty.slug,
            Specialty.name,
            func.count(ProfessionalPresence.professional_user_id).label("online_count"),
        )
        .outerjoin(
            ProfessionalSpecialty,
            ProfessionalSpecialty.specialty_id == Specialty.id,
        )
        .outerjoin(
            ProfessionalPresence,
            (
                ProfessionalPresence.professional_user_id
                == ProfessionalSpecialty.professional_user_id
            )
            & ProfessionalPresence.is_online.is_(True)
            & (ProfessionalPresence.last_seen_at >= cutoff),
        )
        .where(Specialty.active.is_(True))
        .group_by(Specialty.slug, Specialty.name)
        .order_by(Specialty.name)
    )
    rows = result.all()
    items = [
        SpecialtyAvailabilityItem(slug=row.slug, name=row.name, online_count=row.online_count)
        for row in rows
    ]
    return SpecialtyAvailabilityResponse(items=items)
