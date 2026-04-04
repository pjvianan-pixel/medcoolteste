"""Dynamic pricing engine for specialty consultations."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty_pricing import SpecialtyPricing


@dataclass
class PricingResult:
    suggested_price_cents: int
    base_price_cents: int
    min_price_cents: int
    max_price_cents: int
    supply: int
    demand: int
    supply_demand_ratio: float
    multiplier: float


_MULTIPLIER_MIN = 1.0
_MULTIPLIER_MAX = 1.5
_QUOTE_TTL_MINUTES = 5


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _round_to_90(cents: int) -> int:
    """Round price to nearest *90 ending (e.g. 16990).

    Uses floor-to-90 within the same hundred block.
    """
    hundred = (cents // 100) * 100
    candidate = hundred + 90
    # If the candidate is above the value, use the previous block's 90
    if candidate > cents:
        candidate -= 100
    # Clamp to 0 minimum
    return max(candidate, 90)


async def calculate_price(
    specialty_id: uuid.UUID,
    db: AsyncSession,
    demand: int = 0,
) -> PricingResult:
    """Calculate the dynamic price for a specialty.

    Parameters
    ----------
    specialty_id:
        UUID of the specialty.
    db:
        Async database session.
    demand:
        Number of active pending requests for this specialty.
        Defaults to 0 when there are no active orders yet (MVP stage).
    """
    pricing_result = await db.execute(
        select(SpecialtyPricing).where(SpecialtyPricing.specialty_id == specialty_id)
    )
    pricing = pricing_result.scalar_one_or_none()
    if pricing is None:
        raise ValueError(f"No pricing configured for specialty {specialty_id}")

    # --- supply: online professionals in this specialty ---
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.PRESENCE_TIMEOUT_SECONDS)
    supply_result = await db.execute(
        select(func.count(ProfessionalPresence.professional_user_id))
        .join(
            ProfessionalSpecialty,
            ProfessionalSpecialty.professional_user_id
            == ProfessionalPresence.professional_user_id,
        )
        .where(
            ProfessionalSpecialty.specialty_id == specialty_id,
            ProfessionalPresence.is_online.is_(True),
            ProfessionalPresence.last_seen_at >= cutoff,
        )
    )
    supply = supply_result.scalar() or 0

    # --- dynamic pricing formula ---
    supply_demand_ratio = (demand + 1) / (supply + 1)
    multiplier = _clamp(supply_demand_ratio, _MULTIPLIER_MIN, _MULTIPLIER_MAX)
    raw_price = pricing.base_price_cents * multiplier
    clamped_price = int(_clamp(raw_price, pricing.min_price_cents, pricing.max_price_cents))
    suggested_price = _round_to_90(clamped_price)
    # Make sure rounding didn't break the bounds
    suggested_price = int(_clamp(suggested_price, pricing.min_price_cents, pricing.max_price_cents))

    return PricingResult(
        suggested_price_cents=suggested_price,
        base_price_cents=pricing.base_price_cents,
        min_price_cents=pricing.min_price_cents,
        max_price_cents=pricing.max_price_cents,
        supply=supply,
        demand=demand,
        supply_demand_ratio=supply_demand_ratio,
        multiplier=multiplier,
    )


def quote_expires_at() -> datetime:
    return datetime.now(tz=UTC) + timedelta(minutes=_QUOTE_TTL_MINUTES)
