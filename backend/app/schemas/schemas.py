import uuid
from datetime import date, datetime

from pydantic import BaseModel, EmailStr, field_validator

from app.db.models.consult_offer import ActorRole, ConsultOfferStatus, CounterStatus, EventType
from app.db.models.consult_quote import QuoteStatus
from app.db.models.consult_request import ConsultRequestStatus
from app.db.models.professional_profile import VerificationStatus
from app.db.models.user import UserRole

# ── Auth ────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── User (me) ────────────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Patient profile ──────────────────────────────────────────────────────────

class PatientProfileResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    date_of_birth: date | None
    cpf: str
    phone: str | None

    model_config = {"from_attributes": True}


class PatientProfileUpdate(BaseModel):
    full_name: str | None = None
    date_of_birth: date | None = None
    phone: str | None = None


# ── Professional profile ─────────────────────────────────────────────────────

class ProfessionalProfileResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    crm: str
    specialty: str
    bio: str | None
    is_available: bool

    model_config = {"from_attributes": True}


class ProfessionalProfileUpdate(BaseModel):
    full_name: str | None = None
    specialty: str | None = None
    bio: str | None = None
    is_available: bool | None = None


# ── Admin ────────────────────────────────────────────────────────────────────

class AdminProfessionalResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    crm: str
    specialty: str
    bio: str | None
    is_available: bool
    status_verificacao: VerificationStatus
    verification_reason: str | None

    model_config = {"from_attributes": True}


class RejectRequest(BaseModel):
    reason: str


# ── Specialty ────────────────────────────────────────────────────────────────

class SpecialtyResponse(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    active: bool

    model_config = {"from_attributes": True}


class SpecialtyCreate(BaseModel):
    slug: str
    name: str
    active: bool = True


class SpecialtyUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None


class ProfessionalSpecialtiesUpdate(BaseModel):
    """Body for PUT /professionals/me/specialties.

    Accepts a list of specialty UUIDs or slugs.
    """

    specialties: list[str]


# ── Presence ─────────────────────────────────────────────────────────────────


class PresenceResponse(BaseModel):
    professional_user_id: uuid.UUID
    is_online: bool
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class SpecialtyAvailabilityItem(BaseModel):
    slug: str
    name: str
    online_count: int


class SpecialtyAvailabilityResponse(BaseModel):
    items: list[SpecialtyAvailabilityItem]


# ── Specialty Pricing ─────────────────────────────────────────────────────────


class SpecialtyPricingResponse(BaseModel):
    id: uuid.UUID
    specialty_id: uuid.UUID
    base_price_cents: int
    min_price_cents: int
    max_price_cents: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SpecialtyPricingUpdate(BaseModel):
    base_price_cents: int | None = None
    min_price_cents: int | None = None
    max_price_cents: int | None = None


# ── Consult Quote ─────────────────────────────────────────────────────────────


class QuoteRequest(BaseModel):
    specialty_id: uuid.UUID


class QuoteResponse(BaseModel):
    id: uuid.UUID
    patient_user_id: uuid.UUID
    specialty_id: uuid.UUID
    quoted_price_cents: int
    currency: str
    created_at: datetime
    expires_at: datetime
    status: QuoteStatus

    model_config = {"from_attributes": True}


# ── Consult Request ───────────────────────────────────────────────────────────


class ConsultOfferEventResponse(BaseModel):
    id: uuid.UUID
    consult_offer_id: uuid.UUID
    actor_role: ActorRole
    event_type: EventType
    price_cents: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConsultOfferResponse(BaseModel):
    id: uuid.UUID
    consult_request_id: uuid.UUID
    professional_user_id: uuid.UUID
    price_cents: int
    status: ConsultOfferStatus
    sent_at: datetime
    responded_at: datetime | None
    counter_status: CounterStatus
    counter_price_cents: int | None
    counter_proposed_at: datetime | None
    counter_responded_at: datetime | None
    events: list[ConsultOfferEventResponse] = []

    model_config = {"from_attributes": True}


class CounterOfferRequest(BaseModel):
    price_cents: int


class ConsultRequestCreate(BaseModel):
    quote_id: uuid.UUID
    complaint: str

    @field_validator("complaint")
    @classmethod
    def complaint_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("complaint must not be empty")
        if len(v) > 500:
            raise ValueError("complaint must be at most 500 characters")
        return v


class ConsultRequestResponse(BaseModel):
    id: uuid.UUID
    patient_user_id: uuid.UUID
    specialty_id: uuid.UUID
    quote_id: uuid.UUID
    complaint: str
    status: ConsultRequestStatus
    matched_professional_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    offers: list[ConsultOfferResponse] = []

    model_config = {"from_attributes": True}

