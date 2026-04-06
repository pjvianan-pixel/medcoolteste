import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_role
from app.db.models.consult_offer import (
    ActorRole,
    ConsultOffer,
    ConsultOfferEvent,
    ConsultOfferStatus,
    CounterStatus,
    EventType,
)
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.medical_document import MedicalDocument
from app.db.models.payment import Payment
from app.db.models.professional_presence import ProfessionalPresence
from app.db.models.professional_profile import ProfessionalProfile
from app.db.models.professional_specialty import ProfessionalSpecialty
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    ConsultOfferResponse,
    ConsultRequestResponse,
    CounterOfferRequest,
    ExamRequestCreate,
    MedicalDocumentResponse,
    PaymentResponse,
    PresenceResponse,
    PrescriptionCreate,
    ProfessionalConsultHistoryDocumentSummary,
    ProfessionalConsultHistoryItem,
    ProfessionalConsultHistoryResponse,
    ProfessionalConsultPaymentSummary,
    ProfessionalConsultPayoutSummary,
    ProfessionalFinancialSummaryResponse,
    ProfessionalFinancialTransactionItem,
    ProfessionalFinancialTransactionsResponse,
    ProfessionalProfileResponse,
    ProfessionalProfileUpdate,
    ProfessionalSpecialtiesUpdate,
    SpecialtyResponse,
)
from app.services.cancellation import cancel_by_professional, mark_no_show
from app.services.medical_documents import (
    create_exam_request_for_consult,
    create_prescription_for_consult,
    list_documents_for_consult,
    sign_medical_document,
)
from app.services.professional_financials import (
    FinancialStatus,
    get_professional_financial_summary,
    list_professional_transactions,
)
from app.services.professional_history import (
    get_professional_consult_detail,
    list_professional_consult_history,
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


# ── Offers ────────────────────────────────────────────────────────────────────


@router.get("/me/offers", response_model=list[ConsultOfferResponse])
async def list_pending_offers(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> list[ConsultOffer]:
    """List all pending offers for the authenticated professional."""
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(
            ConsultOffer.professional_user_id == current_user.id,
            ConsultOffer.status == ConsultOfferStatus.pending,
        )
    )
    return list(result.scalars().all())


@router.post(
    "/me/offers/{offer_id}/accept",
    response_model=ConsultOfferResponse,
)
async def accept_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Accept a pending offer.

    - Marks the offer as accepted.
    - Updates the consult_request to matched and sets matched_professional_user_id.
    - Expires all other pending offers for the same consult_request.
    """
    offer_result = await db.execute(
        select(ConsultOffer).where(
            ConsultOffer.id == offer_id,
            ConsultOffer.professional_user_id == current_user.id,
        )
    )
    offer = offer_result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    if offer.status != ConsultOfferStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Offer is not pending",
        )

    now = datetime.now(tz=UTC)
    offer.status = ConsultOfferStatus.accepted
    offer.responded_at = now

    # Update the consult_request
    request_result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == offer.consult_request_id)
    )
    consult_request = request_result.scalar_one()
    consult_request.status = ConsultRequestStatus.matched
    consult_request.matched_professional_user_id = current_user.id

    # Expire other pending offers for the same request
    await db.execute(
        update(ConsultOffer)
        .where(
            ConsultOffer.consult_request_id == offer.consult_request_id,
            ConsultOffer.id != offer_id,
            ConsultOffer.status == ConsultOfferStatus.pending,
        )
        .values(status=ConsultOfferStatus.expired, responded_at=now)
    )

    await db.commit()
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
    )
    return result.scalar_one()


@router.post(
    "/me/offers/{offer_id}/reject",
    response_model=ConsultOfferResponse,
)
async def reject_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Reject a pending offer."""
    offer_result = await db.execute(
        select(ConsultOffer).where(
            ConsultOffer.id == offer_id,
            ConsultOffer.professional_user_id == current_user.id,
        )
    )
    offer = offer_result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    if offer.status != ConsultOfferStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Offer is not pending",
        )

    offer.status = ConsultOfferStatus.rejected
    offer.responded_at = datetime.now(tz=UTC)
    await db.commit()
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
    )
    return result.scalar_one()


@router.post(
    "/me/offers/{offer_id}/counter",
    response_model=ConsultOfferResponse,
    status_code=status.HTTP_200_OK,
)
async def create_counter_offer(
    offer_id: uuid.UUID,
    body: CounterOfferRequest,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Send a counter offer to the patient.

    - Offer must be pending.
    - ConsultRequest must not yet be matched.
    - Sets counter_status=pending and creates a counter_proposed event.
    """
    offer_result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(
            ConsultOffer.id == offer_id,
            ConsultOffer.professional_user_id == current_user.id,
        )
    )
    offer = offer_result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    if offer.status != ConsultOfferStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Offer is not pending",
        )

    request_result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == offer.consult_request_id)
    )
    consult_request = request_result.scalar_one()
    if consult_request.status == ConsultRequestStatus.matched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consult request is already matched",
        )

    now = datetime.now(tz=UTC)
    offer.counter_status = CounterStatus.pending
    offer.counter_price_cents = body.price_cents
    offer.counter_proposed_at = now

    event = ConsultOfferEvent(
        id=uuid.uuid4(),
        consult_offer_id=offer.id,
        actor_role=ActorRole.professional,
        event_type=EventType.counter_proposed,
        price_cents=body.price_cents,
        created_at=now,
    )
    db.add(event)

    await db.commit()
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# ── Payments ──────────────────────────────────────────────────────────────────


@router.get("/me/payments", response_model=list[PaymentResponse])
async def list_professional_payments(
    page: int = 1,
    limit: int = 20,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> list[Payment]:
    """List payments for consult requests matched to the authenticated professional.

    Supports simple page/limit pagination.
    """
    if page < 1:
        page = 1
    offset = (page - 1) * limit

    result = await db.execute(
        select(Payment)
        .where(Payment.professional_user_id == current_user.id)
        .order_by(Payment.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


# ── Cancellation & No-Show ────────────────────────────────────────────────────


@router.post(
    "/me/consult-requests/{request_id}/cancel",
    response_model=ConsultRequestResponse,
)
async def cancel_consult_request_by_professional(
    request_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Cancel a matched consult request (professional only).

    Always triggers a full refund to the patient.
    """
    result = await db.execute(
        select(ConsultRequest)
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.matched_professional_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )

    if consult_request.status != ConsultRequestStatus.matched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only matched consult requests can be cancelled by the professional",
        )

    try:
        await cancel_by_professional(consult_request, db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    await db.commit()
    reload_result = await db.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events))
        .where(ConsultRequest.id == consult_request.id)
        .execution_options(populate_existing=True)
    )
    return reload_result.scalar_one()


@router.post(
    "/me/consult-requests/{request_id}/no-show",
    response_model=ConsultRequestResponse,
)
async def mark_patient_no_show(
    request_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Mark a consult request as patient no-show (professional only).

    Can only be called after ``scheduled_at + grace_minutes`` has elapsed.
    Applies the platform no-show refund policy.
    """
    result = await db.execute(
        select(ConsultRequest)
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.matched_professional_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )

    if consult_request.status != ConsultRequestStatus.matched:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only matched consult requests can be marked as no-show",
        )

    try:
        await mark_no_show(consult_request, db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    await db.commit()
    reload_result = await db.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events))
        .where(ConsultRequest.id == consult_request.id)
        .execution_options(populate_existing=True)
    )
    return reload_result.scalar_one()


# ── Financial Statement ───────────────────────────────────────────────────────


@router.get("/me/financial/summary", response_model=ProfessionalFinancialSummaryResponse)
async def get_financial_summary(
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalFinancialSummaryResponse:
    """Return the financial summary for the authenticated professional.

    - ``total_received``: sum of professional_amount for paid transactions.
    - ``total_pending``: sum for pending/refund_pending transactions.
    - ``total_refunded``: sum for refunded transactions.
    """
    summary = await get_professional_financial_summary(current_user.id, db)
    return ProfessionalFinancialSummaryResponse(
        total_received=summary.total_received,
        total_pending=summary.total_pending,
        total_refunded=summary.total_refunded,
    )


@router.get(
    "/me/financial/transactions",
    response_model=ProfessionalFinancialTransactionsResponse,
)
async def get_financial_transactions(
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    from_date: Optional[datetime] = Query(default=None, description="Filter payments created on or after this datetime (ISO 8601)"),
    to_date: Optional[datetime] = Query(default=None, description="Filter payments created on or before this datetime (ISO 8601)"),
    financial_status: Optional[FinancialStatus] = Query(default=None, description="Filter by financial status"),
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalFinancialTransactionsResponse:
    """Return a paginated list of financial transactions for the authenticated professional.

    Optional filters:
    - ``from_date`` / ``to_date``: filter by payment creation date.
    - ``financial_status``: filter by ``pending``, ``paid``, ``refund_pending``,
      ``refunded``, or ``canceled``.
    """
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_date must not be after to_date",
        )
    items, total = await list_professional_transactions(
        current_user.id,
        db,
        from_date=from_date,
        to_date=to_date,
        financial_status=financial_status,
        page=page,
        limit=limit,
    )
    return ProfessionalFinancialTransactionsResponse(
        items=[
            ProfessionalFinancialTransactionItem(
                consult_request_id=item.consult_request_id,
                payment_id=item.payment_id,
                scheduled_at=item.scheduled_at,
                created_at=item.created_at,
                amount_total=item.amount_total,
                platform_fee_amount=item.platform_fee_amount,
                professional_amount=item.professional_amount,
                financial_status=item.financial_status,
            )
            for item in items
        ],
        total=total,
        page=page,
        limit=limit,
    )


# ── F5 Part 1 – Medical Documents ─────────────────────────────────────────────


@router.post(
    "/me/consult-requests/{consult_id}/prescriptions",
    response_model=MedicalDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a prescription for a consult",
)
async def create_prescription(
    consult_id: uuid.UUID,
    body: PrescriptionCreate,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> MedicalDocumentResponse:
    """Create a prescription document linked to the given consult request.

    Only the professional matched to the consult may create documents for it.
    The consult must be in ``matched`` or ``no_show_patient`` status.
    """
    return await create_prescription_for_consult(db, consult_id, current_user, body)


@router.post(
    "/me/consult-requests/{consult_id}/exam-requests",
    response_model=MedicalDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an exam request for a consult",
)
async def create_exam_request(
    consult_id: uuid.UUID,
    body: ExamRequestCreate,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> MedicalDocumentResponse:
    """Create an exam request (lab or imaging) linked to the given consult request.

    Only the professional matched to the consult may create documents for it.
    The consult must be in ``matched`` or ``no_show_patient`` status.
    """
    return await create_exam_request_for_consult(db, consult_id, current_user, body)


@router.get(
    "/me/consult-requests/{consult_id}/documents",
    response_model=list[MedicalDocumentResponse],
    summary="List all documents for a consult",
)
async def list_consult_documents(
    consult_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> list[MedicalDocumentResponse]:
    """Return all medical documents (prescriptions and exam requests) for a consult.

    Only the professional matched to the consult may access its documents.
    """
    return await list_documents_for_consult(db, consult_id, current_user)


# ── F5 Part 2 – Document signing ──────────────────────────────────────────────


@router.post(
    "/me/documents/{document_id}/sign",
    response_model=MedicalDocumentResponse,
    summary="Sign a medical document",
)
async def sign_document(
    document_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> MedicalDocumentResponse:
    """Sign a DRAFT medical document (prescription or exam request).

    - Only the professional who created the document may sign it.
    - Document must be in DRAFT status.
    - Transitions the document to SIGNED, sets ``signature_type = SIMPLE``,
      records ``signed_at``, generates a PDF and stores its URL in ``file_url``.
    """
    return await sign_medical_document(db, document_id, current_user)


# ── F6 Part 2 – Professional consult history ─────────────────────────────────


@router.get(
    "/me/history/consults",
    response_model=ProfessionalConsultHistoryResponse,
    summary="Professional consult history (paginated)",
)
async def get_professional_history(
    from_date: datetime | None = Query(default=None, description="Filter consults created on/after this date"),
    to_date: datetime | None = Query(default=None, description="Filter consults created on/before this date"),
    consult_status: str | None = Query(default=None, description="Filter by consult status"),
    payment_status: str | None = Query(default=None, description="Filter by financial status (pending/paid/refund_pending/refunded/canceled)"),
    has_payout: bool | None = Query(default=None, description="Filter by payout presence"),
    patient_name: str | None = Query(default=None, description="Case-insensitive substring filter on patient name"),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalConsultHistoryResponse:
    """Return the authenticated professional's paginated consult history.

    Each item aggregates consult info, patient name, payment summary (with
    payout details), and associated medical documents.  Only read operations
    are performed.
    """
    parsed_consult_status: ConsultRequestStatus | None = None
    if consult_status is not None:
        try:
            parsed_consult_status = ConsultRequestStatus(consult_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid consult_status '{consult_status}'",
            )

    parsed_payment_status: FinancialStatus | None = None
    if payment_status is not None:
        try:
            parsed_payment_status = FinancialStatus(payment_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid payment_status '{payment_status}'",
            )

    page_result = await list_professional_consult_history(
        professional_user_id=current_user.id,
        db=db,
        from_date=from_date,
        to_date=to_date,
        consult_status=parsed_consult_status,
        payment_status=parsed_payment_status,
        has_payout=has_payout,
        patient_name=patient_name,
        page=page,
        limit=limit,
    )

    items = [_map_pro_history_item(item) for item in page_result.items]

    return ProfessionalConsultHistoryResponse(
        items=items,
        total=page_result.total,
        page=page_result.page,
        limit=page_result.limit,
    )


@router.get(
    "/me/history/consults/{consult_id}",
    response_model=ProfessionalConsultHistoryItem,
    summary="Get a single consult history item (professional)",
)
async def get_professional_history_detail(
    consult_id: uuid.UUID,
    current_user: User = Depends(_professional_dep),
    db: AsyncSession = Depends(get_db),
) -> ProfessionalConsultHistoryItem:
    """Return the history detail for a single consult belonging to the professional.

    Returns 404 (not 403) when the consult belongs to a different professional,
    to avoid leaking existence of other professionals' consults.
    """
    item = await get_professional_consult_detail(
        professional_user_id=current_user.id,
        consult_id=consult_id,
        db=db,
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consult request not found",
        )
    return _map_pro_history_item(item)


# ── Private mapping helpers ───────────────────────────────────────────────────


def _map_pro_history_item(item) -> ProfessionalConsultHistoryItem:  # type: ignore[return]
    payment = None
    if item.payment is not None:
        payment = ProfessionalConsultPaymentSummary(
            status=item.payment.status,
            financial_status=item.payment.financial_status.value,
            amount_total_cents=item.payment.amount_total_cents,
            professional_amount_cents=item.payment.professional_amount_cents,
            platform_fee_cents=item.payment.platform_fee_cents,
            refunded_amount_cents=item.payment.refunded_amount_cents,
        )
    payout = None
    if item.payout is not None:
        payout = ProfessionalConsultPayoutSummary(
            payout_id=item.payout.payout_id,
            paid_out_at=item.payout.paid_out_at,
        )
    documents = [
        ProfessionalConsultHistoryDocumentSummary(
            id=d.id,
            document_type=d.document_type,
            status=d.status,
            created_at=d.created_at,
            file_url=d.file_url,
            summary=d.summary,
        )
        for d in item.documents
    ]
    return ProfessionalConsultHistoryItem(
        consult_id=item.consult_id,
        created_at=item.created_at,
        scheduled_at=item.scheduled_at,
        status=item.status,
        specialty_id=item.specialty_id,
        patient_name=item.patient_name,
        payment=payment,
        payout=payout,
        documents=documents,
    )
