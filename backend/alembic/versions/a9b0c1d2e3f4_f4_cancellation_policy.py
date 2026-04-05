"""f4 part 3 - cancellation policy and no-show

Adds new ConsultRequest statuses, scheduling/cancellation timestamps,
Payment.provider_charge_id, and Payment.refund_pending status.

Revision ID: a9b0c1d2e3f4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-05 19:00:00.000000

Notes on PostgreSQL enum updates
---------------------------------
PostgreSQL does not allow adding values to an enum type inside a transaction
(prior to PG 12).  The ``ALTER TYPE ... ADD VALUE`` statements below must be
run **outside** a transaction block.  Alembic's default behaviour is to wrap
each migration in a single transaction; to accommodate this the migration uses
``op.get_bind().execute()`` which Alembic can be configured to run in
autocommit mode via ``transaction_per_migration = true`` in ``alembic.ini``, or
by upgrading to PostgreSQL 12+ where the restriction is lifted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # ── Extend consult_request_status enum ──────────────────────────────────
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "ALTER TYPE consult_request_status ADD VALUE IF NOT EXISTS "
                "'cancelled_by_patient'"
            )
        )
        bind.execute(
            sa.text(
                "ALTER TYPE consult_request_status ADD VALUE IF NOT EXISTS "
                "'cancelled_by_professional'"
            )
        )
        bind.execute(
            sa.text(
                "ALTER TYPE consult_request_status ADD VALUE IF NOT EXISTS "
                "'no_show_patient'"
            )
        )

    # ── Add scheduling / cancellation columns to consult_requests ───────────
    op.add_column(
        "consult_requests",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "consult_requests",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "consult_requests",
        sa.Column("no_show_marked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Extend payment_status enum ───────────────────────────────────────────
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'refund_pending'"
            )
        )

    # ── Add provider_charge_id to payments ───────────────────────────────────
    op.add_column(
        "payments",
        sa.Column("provider_charge_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "provider_charge_id")
    op.drop_column("consult_requests", "no_show_marked_at")
    op.drop_column("consult_requests", "cancelled_at")
    op.drop_column("consult_requests", "scheduled_at")

    # PostgreSQL does not support removing enum values; downgrade leaves the
    # new enum values in place but they won't be used by the application.
