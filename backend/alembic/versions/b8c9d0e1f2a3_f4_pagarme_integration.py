"""f4 pagarme integration – add checkout_url, gateway_event_id, pagarme_recipient_id

Revision ID: b8c9d0e1f2a3
Revises: f6a7b8c9d0e1
Create Date: 2026-04-05 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # payments: add checkout_url for PIX / payment link URLs
    op.add_column("payments", sa.Column("checkout_url", sa.Text(), nullable=True))

    # payment_events: add gateway_event_id for idempotent webhook processing
    op.add_column(
        "payment_events",
        sa.Column("gateway_event_id", sa.String(255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_payment_events_gateway_event_id",
        "payment_events",
        ["gateway_event_id"],
    )

    # professional_profiles: add pagarme_recipient_id for split payments
    op.add_column(
        "professional_profiles",
        sa.Column("pagarme_recipient_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("professional_profiles", "pagarme_recipient_id")
    op.drop_constraint(
        "uq_payment_events_gateway_event_id",
        "payment_events",
        type_="unique",
    )
    op.drop_column("payment_events", "gateway_event_id")
    op.drop_column("payments", "checkout_url")
