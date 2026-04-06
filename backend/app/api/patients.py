import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
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
from app.db.models.consult_quote import ConsultQuote, QuoteStatus
from app.db.models.consult_request import ConsultRequest, ConsultRequestStatus
from app.db.models.patient_profile import PatientProfile
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.specialty import Specialty
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.schemas.schemas import (
    ChatMessagePageResponse,
    ChatMessageResponse,
    ConsultOfferResponse,
    ConsultRequestCreate,
    ConsultRequestResponse,
    MedicalDocumentResponse,
    PatientConsultHistoryItem,
    PatientConsultHistoryResponse,
    PatientProfileResponse,
    PatientProfileUpdate,
    PaymentResponse,
    QuoteRequest,
    QuoteResponse,
    VideoSessionResponse,
)
from app.services.cancellation import cancel_by_patient
from app.services.chat import list_chat_messages
from app.services.matching import run_matching
from app.services.medical_documents import get_document_for_patient, list_documents_for_patient
from app.services.patient_history import get_patient_consult_detail, list_patient_consult_history
from app.services.payments import create_payment_for_consult_request
from app.services.pricing import calculate_price, get_demand_for_specialty, quote_expires_at
from app.services.video_sessions import end_video_session, get_video_session

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found"
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient profile not found"
        )

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post("/me/quotes", response_model=QuoteResponse, status_code=status.HTTP_201_CREATED)
async def create_quote(
    body: QuoteRequest,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultQuote:
    """Generate a fixed-price quote for a specialty (patient only).

    The quoted price is calculated at request time using the dynamic pricing
    engine and will not change for the lifetime of the quote (5 minutes).
    """
    # Validate specialty exists and is active
    specialty_result = await db.execute(
        select(Specialty).where(Specialty.id == body.specialty_id, Specialty.active.is_(True))
    )
    specialty = specialty_result.scalar_one_or_none()
    if specialty is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialty not found or inactive",
        )

    # Calculate price using real demand from active consult requests
    try:
        demand = await get_demand_for_specialty(body.specialty_id, db)
        pricing_result = await calculate_price(body.specialty_id, db, demand=demand)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    quote = ConsultQuote(
        id=uuid.uuid4(),
        patient_user_id=current_user.id,
        specialty_id=body.specialty_id,
        quoted_price_cents=pricing_result.suggested_price_cents,
        currency="BRL",
        expires_at=quote_expires_at(),
        status=QuoteStatus.active,
    )
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return quote


# ── Consult Requests ──────────────────────────────────────────────────────────


@router.post(
    "/me/consult-requests",
    response_model=ConsultRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_consult_request(
    body: ConsultRequestCreate,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Create a consult request from a valid quote (patient only).

    - Validates quote is active, not expired, and belongs to the patient.
    - Creates the consult_request in status 'queued'.
    - Runs the initial matching and transitions to 'offering' if offers are created.
    - Marks the quote as 'used'.
    """
    # Load quote with eager load check
    quote_result = await db.execute(
        select(ConsultQuote).where(ConsultQuote.id == body.quote_id)
    )
    quote = quote_result.scalar_one_or_none()

    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")

    if quote.patient_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Quote does not belong to this patient")

    if quote.status != QuoteStatus.active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Quote is not active",
        )

    # Ensure timezone-aware comparison (SQLite returns naive datetimes)
    expires_at = quote.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(tz=UTC):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Quote has expired",
        )

    consult_request = ConsultRequest(
        id=uuid.uuid4(),
        patient_user_id=current_user.id,
        specialty_id=quote.specialty_id,
        quote_id=quote.id,
        complaint=body.complaint,
        status=ConsultRequestStatus.queued,
    )
    db.add(consult_request)

    # Mark quote as used
    quote.status = QuoteStatus.used

    await db.flush()

    # Eager-load the quote relationship needed by matching
    await db.refresh(consult_request)
    consult_request.quote = quote

    # Run initial matching
    await run_matching(consult_request, db)

    await db.commit()

    # Reload with offers and events
    reload_result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(ConsultRequest.id == consult_request.id)
    )
    return reload_result.scalar_one()


@router.get(
    "/me/consult-requests/{request_id}",
    response_model=ConsultRequestResponse,
)
async def get_consult_request(
    request_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Get a specific consult request with its status, offers, and matched professional."""
    result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.patient_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )
    return consult_request


@router.post(
    "/me/consult-requests/{request_id}/cancel",
    response_model=ConsultRequestResponse,
)
async def cancel_consult_request(
    request_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultRequest:
    """Cancel a consult request (patient only).

    For unmatched requests (queued/offering) the request is simply cancelled.
    For matched requests the cancellation policy is applied:
    - Early cancellation (≥ min_hours_full_refund before scheduled_at): full refund.
    - Late cancellation: partial or no refund per policy.
    """
    result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.patient_user_id == current_user.id,
        )
    )
    consult_request = result.scalar_one_or_none()
    if consult_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Consult request not found"
        )

    terminal_statuses = {
        ConsultRequestStatus.canceled,
        ConsultRequestStatus.cancelled_by_patient,
        ConsultRequestStatus.cancelled_by_professional,
        ConsultRequestStatus.no_show_patient,
        ConsultRequestStatus.expired,
    }
    if consult_request.status in terminal_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consult request is already cancelled or in a terminal state",
        )

    if consult_request.status == ConsultRequestStatus.matched:
        try:
            await cancel_by_patient(consult_request, db)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    else:
        # queued or offering: simple pre-payment cancellation
        consult_request.status = ConsultRequestStatus.canceled

    await db.commit()
    # Reload to get fresh state including offers
    reload_result = await db.execute(
        select(ConsultRequest)
        .options(
            selectinload(ConsultRequest.offers).selectinload(ConsultOffer.events)
        )
        .where(ConsultRequest.id == consult_request.id)
        .execution_options(populate_existing=True)
    )
    return reload_result.scalar_one()


async def _get_offer_for_patient(
    offer_id: uuid.UUID,
    patient_user_id: uuid.UUID,
    db: AsyncSession,
) -> ConsultOffer:
    """Load an offer and verify it belongs to a consult_request owned by the patient."""
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .join(ConsultRequest, ConsultRequest.id == ConsultOffer.consult_request_id)
        .where(
            ConsultOffer.id == offer_id,
            ConsultRequest.patient_user_id == patient_user_id,
        )
    )
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")
    return offer


@router.post(
    "/me/offers/{offer_id}/counter/accept",
    response_model=ConsultOfferResponse,
    status_code=status.HTTP_200_OK,
)
async def accept_counter_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Accept a professional's counter offer.

    - counter_status must be pending.
    - Marks counter_status=accepted, offer.status=accepted.
    - Marks consult_request as matched.
    - Expires all other pending offers for the same request.
    - Creates a counter_accepted event.
    """
    offer = await _get_offer_for_patient(offer_id, current_user.id, db)

    if offer.counter_status != CounterStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No pending counter offer on this offer",
        )

    now = datetime.now(tz=UTC)
    offer.counter_status = CounterStatus.accepted
    offer.counter_responded_at = now
    offer.status = ConsultOfferStatus.accepted
    offer.responded_at = now

    request_result = await db.execute(
        select(ConsultRequest).where(ConsultRequest.id == offer.consult_request_id)
    )
    consult_request = request_result.scalar_one()
    consult_request.status = ConsultRequestStatus.matched
    consult_request.matched_professional_user_id = offer.professional_user_id

    # Expire other pending offers
    await db.execute(
        update(ConsultOffer)
        .where(
            ConsultOffer.consult_request_id == offer.consult_request_id,
            ConsultOffer.id != offer_id,
            ConsultOffer.status == ConsultOfferStatus.pending,
        )
        .values(status=ConsultOfferStatus.expired, responded_at=now)
    )

    event = ConsultOfferEvent(
        id=uuid.uuid4(),
        consult_offer_id=offer.id,
        actor_role=ActorRole.patient,
        event_type=EventType.counter_accepted,
        price_cents=None,
        created_at=now,
    )
    db.add(event)

    await db.commit()
    # Expire cached identity so reload fetches fresh data including new event
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


@router.post(
    "/me/offers/{offer_id}/counter/reject",
    response_model=ConsultOfferResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_counter_offer(
    offer_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ConsultOffer:
    """Reject a professional's counter offer.

    - counter_status must be pending.
    - Marks counter_status=rejected, offer.status=rejected.
    - Creates a counter_rejected event.
    - If no other pending offers remain for the consult_request, re-runs matching.
    """
    offer = await _get_offer_for_patient(offer_id, current_user.id, db)

    if offer.counter_status != CounterStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No pending counter offer on this offer",
        )

    now = datetime.now(tz=UTC)
    offer.counter_status = CounterStatus.rejected
    offer.counter_responded_at = now
    offer.status = ConsultOfferStatus.rejected
    offer.responded_at = now

    event = ConsultOfferEvent(
        id=uuid.uuid4(),
        consult_offer_id=offer.id,
        actor_role=ActorRole.patient,
        event_type=EventType.counter_rejected,
        price_cents=None,
        created_at=now,
    )
    db.add(event)

    await db.flush()

    # Check if any pending offers remain
    pending_result = await db.execute(
        select(ConsultOffer).where(
            ConsultOffer.consult_request_id == offer.consult_request_id,
            ConsultOffer.status == ConsultOfferStatus.pending,
            ConsultOffer.id != offer_id,
        )
    )
    remaining_pending = list(pending_result.scalars().all())

    if not remaining_pending:
        request_result = await db.execute(
            select(ConsultRequest).where(ConsultRequest.id == offer.consult_request_id)
        )
        consult_request = request_result.scalar_one()
        if consult_request.status != ConsultRequestStatus.matched:
            # Reload quote for matching
            from app.db.models.consult_quote import ConsultQuote  # noqa: PLC0415

            quote_result = await db.execute(
                select(ConsultQuote).where(ConsultQuote.id == consult_request.quote_id)
            )
            consult_request.quote = quote_result.scalar_one()
            await run_matching(consult_request, db)

    await db.commit()
    # Expire cached identity so reload fetches fresh data including new event
    result = await db.execute(
        select(ConsultOffer)
        .options(selectinload(ConsultOffer.events))
        .where(ConsultOffer.id == offer.id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# ── Payments ──────────────────────────────────────────────────────────────────


@router.post(
    "/me/consult-requests/{request_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment(
    request_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> Payment:
    """Create a payment for a matched consult request (patient only).

    - The consult_request must belong to the authenticated patient.
    - The consult_request must be in 'matched' status.
    - No active payment (pending/processing/paid) must already exist for it.
    """
    result = await db.execute(
        select(ConsultRequest)
        .options(selectinload(ConsultRequest.quote))
        .where(
            ConsultRequest.id == request_id,
            ConsultRequest.patient_user_id == current_user.id,
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
            detail="Consult request is not matched yet",
        )

    # Check for existing active payment
    existing_result = await db.execute(
        select(Payment).where(
            Payment.consult_request_id == request_id,
            Payment.status.in_(
                [PaymentStatus.pending, PaymentStatus.processing, PaymentStatus.paid]
            ),
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An active payment already exists for this consult request",
        )

    payment = await create_payment_for_consult_request(consult_request, db)
    await db.commit()
    await db.refresh(payment)
    return payment


@router.get("/me/payments/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> Payment:
    """Get details of a specific payment belonging to the authenticated patient."""
    result = await db.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.patient_user_id == current_user.id,
        )
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    return payment


# ── F5 Part 2 – Patient document access ──────────────────────────────────────


@router.get(
    "/me/consult-requests/{consult_id}/documents",
    response_model=list[MedicalDocumentResponse],
    summary="List signed documents for a consult (patient)",
)
async def list_patient_consult_documents(
    consult_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> list[MedicalDocumentResponse]:
    """Return all SIGNED medical documents for a consult belonging to the patient.

    Only signed documents are exposed; drafts are not visible to patients.
    """
    return await list_documents_for_patient(db, consult_id, current_user)


@router.get(
    "/me/documents/{document_id}",
    response_model=MedicalDocumentResponse,
    summary="Get a specific signed document (patient)",
)
async def get_patient_document(
    document_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> MedicalDocumentResponse:
    """Return a single SIGNED medical document accessible to the authenticated patient.

    Use ``file_url`` in the response to download the generated PDF.
    """
    return await get_document_for_patient(db, document_id, current_user)


# ── F6 Part 1 – Patient consult history ──────────────────────────────────────


@router.get(
    "/me/history/consults",
    response_model=PatientConsultHistoryResponse,
    summary="Patient consult history (paginated)",
)
async def get_patient_history(
    from_date: datetime | None = Query(default=None, description="Filter consults created on/after this date"),
    to_date: datetime | None = Query(default=None, description="Filter consults created on/before this date"),
    consult_status: str | None = Query(default=None, description="Filter by consult status"),
    has_documents: bool | None = Query(default=None, description="Filter by presence of documents"),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> PatientConsultHistoryResponse:
    """Return the authenticated patient's paginated consult history.

    Each item aggregates consult info, payment summary, and associated
    medical documents.  Only read operations are performed.
    """
    parsed_status: ConsultRequestStatus | None = None
    if consult_status is not None:
        try:
            parsed_status = ConsultRequestStatus(consult_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid consult_status '{consult_status}'",
            )

    page_result = await list_patient_consult_history(
        patient_user_id=current_user.id,
        db=db,
        from_date=from_date,
        to_date=to_date,
        consult_status=parsed_status,
        has_documents=has_documents,
        page=page,
        limit=limit,
    )

    items = [
        PatientConsultHistoryItem(
            consult_id=item.consult_id,
            created_at=item.created_at,
            scheduled_at=item.scheduled_at,
            status=item.status,
            specialty_id=item.specialty_id,
            professional_name=item.professional_name,
            professional_specialty=item.professional_specialty,
            professional_crm=item.professional_crm,
            payment=_map_payment_summary(item.payment),
            documents=[
                _map_document_summary(d) for d in item.documents
            ],
        )
        for item in page_result.items
    ]

    return PatientConsultHistoryResponse(
        items=items,
        total=page_result.total,
        page=page_result.page,
        limit=page_result.limit,
    )


@router.get(
    "/me/history/consults/{consult_id}",
    response_model=PatientConsultHistoryItem,
    summary="Get a single consult history item (patient)",
)
async def get_patient_history_detail(
    consult_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> PatientConsultHistoryItem:
    """Return the history detail for a single consult belonging to the patient."""
    item = await get_patient_consult_detail(
        patient_user_id=current_user.id,
        consult_id=consult_id,
        db=db,
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consult request not found",
        )
    return PatientConsultHistoryItem(
        consult_id=item.consult_id,
        created_at=item.created_at,
        scheduled_at=item.scheduled_at,
        status=item.status,
        specialty_id=item.specialty_id,
        professional_name=item.professional_name,
        professional_specialty=item.professional_specialty,
        professional_crm=item.professional_crm,
        payment=_map_payment_summary(item.payment),
        documents=[_map_document_summary(d) for d in item.documents],
    )


# ── Private mapping helpers ───────────────────────────────────────────────────


def _map_payment_summary(payment_data) -> "PatientConsultPaymentSummary | None":  # type: ignore[name-defined]
    from app.schemas.schemas import PatientConsultPaymentSummary  # noqa: PLC0415

    if payment_data is None:
        return None
    return PatientConsultPaymentSummary(
        status=payment_data.status,
        amount_total_cents=payment_data.amount_total_cents,
        refunded_amount_cents=payment_data.refunded_amount_cents,
        method=payment_data.method,
    )


def _map_document_summary(doc_data) -> "PatientConsultHistoryDocumentSummary":  # type: ignore[name-defined]
    from app.schemas.schemas import PatientConsultHistoryDocumentSummary  # noqa: PLC0415

    return PatientConsultHistoryDocumentSummary(
        id=doc_data.id,
        document_type=doc_data.document_type,
        status=doc_data.status,
        created_at=doc_data.created_at,
        file_url=doc_data.file_url,
        summary=doc_data.summary,
    )


# ── Chat history (F3 Part 1) ──────────────────────────────────────────────────


@router.get(
    "/me/consult-requests/{consult_id}/chat/messages",
    response_model=ChatMessagePageResponse,
    summary="List chat messages for a consult (patient)",
)
async def patient_list_chat_messages(
    consult_id: uuid.UUID,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = Query(None, description="Return messages sent before this timestamp"),
    after: datetime | None = Query(None, description="Return messages sent after this timestamp"),
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> ChatMessagePageResponse:
    """Return the paginated chat history for a consult request.

    Only the patient of the consult can access this endpoint.
    Supports infinite-scroll pagination via the ``before``/``after`` filters.
    """
    messages, total = await list_chat_messages(
        db=db,
        consult_id=consult_id,
        user_id=current_user.id,
        page=page,
        limit=limit,
        before=before,
        after=after,
    )
    return ChatMessagePageResponse(
        items=[ChatMessageResponse.model_validate(m) for m in messages],
        total=total,
        page=page,
        limit=limit,
    )


# ── F3 Part 2 – Video session endpoints (patient) ────────────────────────────


@router.get(
    "/me/consult-requests/{consult_id}/video-session",
    response_model=VideoSessionResponse,
    summary="Get the video session for a consult (patient)",
)
async def patient_get_video_session(
    consult_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> VideoSessionResponse:
    """Return the VideoSession provisioned for this consult.

    Returns HTTP 404 if the professional has not created a session yet.
    """
    session, access_token = await get_video_session(
        db=db,
        consult_request_id=consult_id,
        user_id=current_user.id,
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No video session found for this consult request",
        )
    response = VideoSessionResponse.model_validate(session)
    response.access_token = access_token
    return response


@router.post(
    "/me/consult-requests/{consult_id}/video-session/end",
    response_model=VideoSessionResponse,
    summary="End the video session for a consult (patient)",
)
async def patient_end_video_session(
    consult_id: uuid.UUID,
    current_user: User = Depends(_patient_dep),
    db: AsyncSession = Depends(get_db),
) -> VideoSessionResponse:
    """Mark the VideoSession as ENDED and close the Twilio room.

    Either participant may call this endpoint.
    """
    session = await end_video_session(
        db=db,
        consult_request_id=consult_id,
        user_id=current_user.id,
    )
    return VideoSessionResponse.model_validate(session)
