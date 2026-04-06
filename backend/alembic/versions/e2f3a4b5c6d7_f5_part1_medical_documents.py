"""f5_part1_medical_documents

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-04-05 23:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "medical_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "consult_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consult_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "professional_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_type",
            sa.Enum("PRESCRIPTION", "EXAM_REQUEST", name="document_type"),
            nullable=False,
        ),
        sa.Column(
            "subtype",
            sa.Enum("LAB", "IMAGING", name="document_subtype"),
            nullable=True,
        ),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "SIGNED", "CANCELLED", name="document_status"),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column(
            "signature_type",
            sa.Enum("NONE", "SIMPLE", "ICP_BRASIL", name="signature_type"),
            nullable=False,
            server_default="NONE",
        ),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_medical_documents_consult_request_id",
        "medical_documents",
        ["consult_request_id"],
    )
    op.create_index(
        "ix_medical_documents_professional_user_id",
        "medical_documents",
        ["professional_user_id"],
    )
    op.create_index(
        "ix_medical_documents_patient_user_id",
        "medical_documents",
        ["patient_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_medical_documents_patient_user_id", table_name="medical_documents"
    )
    op.drop_index(
        "ix_medical_documents_professional_user_id", table_name="medical_documents"
    )
    op.drop_index(
        "ix_medical_documents_consult_request_id", table_name="medical_documents"
    )
    op.drop_table("medical_documents")
    op.execute("DROP TYPE IF EXISTS document_type")
    op.execute("DROP TYPE IF EXISTS document_subtype")
    op.execute("DROP TYPE IF EXISTS document_status")
    op.execute("DROP TYPE IF EXISTS signature_type")
