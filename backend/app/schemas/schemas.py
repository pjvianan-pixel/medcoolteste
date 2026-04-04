import uuid
from datetime import date, datetime

from pydantic import BaseModel, EmailStr

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
