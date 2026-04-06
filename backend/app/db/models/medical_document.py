import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class DocumentType(enum.StrEnum):
    PRESCRIPTION = "PRESCRIPTION"
    EXAM_REQUEST = "EXAM_REQUEST"


class DocumentSubtype(enum.StrEnum):
    """Optional subtype; mainly used for EXAM_REQUEST to distinguish lab vs imaging."""

    LAB = "LAB"
    IMAGING = "IMAGING"


class DocumentStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SIGNED = "SIGNED"
    CANCELLED = "CANCELLED"


class SignatureType(enum.StrEnum):
    """Placeholder for future digital-signature tiers (Part 2).

    NONE      – document has no signature yet (default).
    SIMPLE    – lightweight electronic signature (e.g., timestamp + professional id).
    ICP_BRASIL – full ICP-Brasil certificate; required by Brazilian law for
                  electronic prescriptions in telemedicine (future integration).
    """

    NONE = "NONE"
    SIMPLE = "SIMPLE"
    ICP_BRASIL = "ICP_BRASIL"


class MedicalDocument(Base):
    """Single model (Option A) covering both prescriptions and exam requests.

    ``content_json`` stores the document's payload as a list of structured items:
      - Prescription: [{drug_name, dosage, instructions, duration_days}, ...]
      - Exam request:  [{exam_name, type, notes}, ...]

    Fields kept for future F5 Part 2 extensibility:
      - ``signature_type`` – will be populated when digital signing is added.
      - ``signed_at``       – timestamp of the signing event.
      - ``file_url``        – URL of the generated PDF once that feature lands.
    """

    __tablename__ = "medical_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    consult_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("consult_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    professional_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        SAEnum(DocumentType, name="document_type"),
        nullable=False,
    )
    subtype: Mapped[DocumentSubtype | None] = mapped_column(
        SAEnum(DocumentSubtype, name="document_subtype"),
        nullable=True,
    )
    # Structured content stored as JSON list of items.
    content_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )
    # TODO (F5 Part 2): populate when digital signing is implemented.
    signature_type: Mapped[SignatureType] = mapped_column(
        SAEnum(SignatureType, name="signature_type"),
        nullable=False,
        default=SignatureType.NONE,
    )
    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # TODO (F5 Part 2): populate with the generated PDF's storage URL.
    file_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    consult_request: Mapped["ConsultRequest"] = relationship(  # noqa: F821
        "ConsultRequest", foreign_keys=[consult_request_id]
    )
    professional: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[professional_user_id]
    )
    patient: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[patient_user_id]
    )
