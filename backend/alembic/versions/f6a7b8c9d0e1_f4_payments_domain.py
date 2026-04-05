"""f4 payments domain

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-05 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "consult_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consult_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "professional_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="BRL"),
        sa.Column("platform_fee_cents", sa.Integer(), nullable=False),
        sa.Column("professional_amount_cents", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("provider_payment_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "paid",
                "refunded",
                "failed",
                "canceled",
                name="payment_status",
            ),
            nullable=False,
            server_default="pending",
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
    op.create_index("ix_payments_consult_request_id", "payments", ["consult_request_id"])
    op.create_index("ix_payments_patient_user_id", "payments", ["patient_user_id"])
    op.create_index("ix_payments_professional_user_id", "payments", ["professional_user_id"])

    op.create_table(
        "payment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "payment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.Enum(
                "created",
                "status_changed",
                "provider_callback",
                "refund_requested",
                "refund_completed",
                name="payment_event_type",
            ),
            nullable=False,
        ),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_events_payment_id", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index("ix_payments_professional_user_id", table_name="payments")
    op.drop_index("ix_payments_patient_user_id", table_name="payments")
    op.drop_index("ix_payments_consult_request_id", table_name="payments")
    op.drop_table("payments")
    op.execute("DROP TYPE IF EXISTS payment_status")
    op.execute("DROP TYPE IF EXISTS payment_event_type")
