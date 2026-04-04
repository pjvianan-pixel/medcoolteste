"""F2 part 4: consult_requests and consult_offers tables

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-04 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consult_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "patient_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "specialty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("specialties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "quote_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consult_quotes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("complaint", sa.String(500), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued", "offering", "matched", "canceled", "expired",
                name="consult_request_status",
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "matched_professional_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    op.create_index("ix_consult_requests_patient_user_id", "consult_requests", ["patient_user_id"])
    op.create_index("ix_consult_requests_specialty_id", "consult_requests", ["specialty_id"])

    op.create_table(
        "consult_offers",
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
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "accepted", "rejected", "expired",
                name="consult_offer_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_consult_offers_consult_request_id", "consult_offers", ["consult_request_id"])
    op.create_index("ix_consult_offers_professional_user_id", "consult_offers", ["professional_user_id"])


def downgrade() -> None:
    op.drop_index("ix_consult_offers_professional_user_id", table_name="consult_offers")
    op.drop_index("ix_consult_offers_consult_request_id", table_name="consult_offers")
    op.drop_table("consult_offers")
    op.execute("DROP TYPE IF EXISTS consult_offer_status")
    op.drop_index("ix_consult_requests_specialty_id", table_name="consult_requests")
    op.drop_index("ix_consult_requests_patient_user_id", table_name="consult_requests")
    op.drop_table("consult_requests")
    op.execute("DROP TYPE IF EXISTS consult_request_status")
