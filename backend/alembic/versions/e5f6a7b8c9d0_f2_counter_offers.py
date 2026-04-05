"""F2 part 5: counter offers and offer events

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-04 17:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add counter offer columns to consult_offers
    op.add_column(
        "consult_offers",
        sa.Column(
            "counter_status",
            sa.Enum("none", "pending", "accepted", "rejected", name="counter_status"),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "consult_offers",
        sa.Column("counter_price_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "consult_offers",
        sa.Column("counter_proposed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "consult_offers",
        sa.Column("counter_responded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create consult_offer_events table
    op.create_table(
        "consult_offer_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "consult_offer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consult_offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_role",
            sa.Enum("professional", "patient", name="actor_role"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.Enum(
                "counter_proposed",
                "counter_accepted",
                "counter_rejected",
                name="event_type",
            ),
            nullable=False,
        ),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_consult_offer_events_consult_offer_id",
        "consult_offer_events",
        ["consult_offer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_consult_offer_events_consult_offer_id",
        table_name="consult_offer_events",
    )
    op.drop_table("consult_offer_events")
    op.drop_column("consult_offers", "counter_responded_at")
    op.drop_column("consult_offers", "counter_proposed_at")
    op.drop_column("consult_offers", "counter_price_cents")
    op.drop_column("consult_offers", "counter_status")
    op.execute("DROP TYPE IF EXISTS counter_status")
    op.execute("DROP TYPE IF EXISTS actor_role")
    op.execute("DROP TYPE IF EXISTS event_type")
