from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.specialty import Specialty
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.schemas import SpecialtyResponse

router = APIRouter(prefix="/specialties", tags=["specialties"])


@router.get("", response_model=list[SpecialtyResponse])
async def list_specialties(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Specialty]:
    """Return all active specialties (any authenticated user)."""
    result = await db.execute(select(Specialty).where(Specialty.active.is_(True)))
    return list(result.scalars().all())
