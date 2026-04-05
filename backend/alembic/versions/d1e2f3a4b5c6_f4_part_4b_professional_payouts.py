"""f4_part_4b – professional_payouts table and payments.payout_id FK

Revision ID: d1e2f3a4b5c6
Revises: a9b0c1d2e3f4
Create Date: 2026-04-05 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create professional_payouts table
    op.create_table(
        "professional_payouts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "professional_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("total_amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "paid_out_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_professional_payouts_professional_user_id",
        "professional_payouts",
        ["professional_user_id"],
    )

    # Add payout_id FK to payments
    op.add_column(
        "payments",
        sa.Column(
            "payout_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("professional_payouts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_payments_payout_id", "payments", ["payout_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_payout_id", table_name="payments")
    op.drop_column("payments", "payout_id")

    op.drop_index(
        "ix_professional_payouts_professional_user_id",
        table_name="professional_payouts",
    )
    op.drop_table("professional_payouts")
