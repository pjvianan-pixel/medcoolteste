"""Matching engine: pairs a consult request with online approved professionals."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.consult_offer import ConsultOffer, ConsultOfferStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile, VerificationStatus
from app.db.models.professional_specialty import ProfessionalSpecialty


async def run_matching(
    consult_request: ConsultRequest,
    db: AsyncSession,
) -> list[ConsultOffer]:
    """Create offers for up to MATCH_OFFER_BATCH_SIZE approved+online professionals.

    Only professionals that:
    - are approved (status_verificacao = approved)
    - are online (is_online=True and last_seen_at within PRESENCE_TIMEOUT_SECONDS)
    - have the required specialty
    - do not already have a pending offer for this request

    are considered. Returns the list of newly created ConsultOffer records.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.PRESENCE_TIMEOUT_SECONDS)

    # Fetch existing offer recipients to avoid duplicates
    existing_result = await db.execute(
        select(ConsultOffer.professional_user_id).where(
            ConsultOffer.consult_request_id == consult_request.id
        )
    )
    already_offered: set[uuid.UUID] = set(existing_result.scalars().all())

    # Find eligible professionals: approved, online, with the right specialty
    eligible_result = await db.execute(
        select(ProfessionalProfile.user_id)
        .join(
            ProfessionalSpecialty,
            ProfessionalSpecialty.professional_user_id == ProfessionalProfile.user_id,
        )
        .join(
            ProfessionalPresence,
            ProfessionalPresence.professional_user_id == ProfessionalProfile.user_id,
        )
        .where(
            ProfessionalSpecialty.specialty_id == consult_request.specialty_id,
            ProfessionalProfile.status_verificacao == VerificationStatus.approved,
            ProfessionalPresence.is_online.is_(True),
            ProfessionalPresence.last_seen_at >= cutoff,
        )
        .limit(settings.MATCH_OFFER_BATCH_SIZE + len(already_offered))
    )
    eligible_ids: list[uuid.UUID] = list(eligible_result.scalars().all())

    new_offers: list[ConsultOffer] = []
    for professional_user_id in eligible_ids:
        if professional_user_id in already_offered:
            continue
        if len(new_offers) >= settings.MATCH_OFFER_BATCH_SIZE:
            break
        offer = ConsultOffer(
            id=uuid.uuid4(),
            consult_request_id=consult_request.id,
            professional_user_id=professional_user_id,
            price_cents=consult_request.quote.quoted_price_cents,
            status=ConsultOfferStatus.pending,
            sent_at=datetime.now(tz=UTC),
        )
        db.add(offer)
        new_offers.append(offer)

    if new_offers:
        consult_request.status = ConsultRequestStatus.offering

    await db.flush()
    return new_offers
