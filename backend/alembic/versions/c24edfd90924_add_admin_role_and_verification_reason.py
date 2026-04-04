"""add admin role and verification_reason

Revision ID: c24edfd90924
Revises: 7e078f080a51
Create Date: 2026-04-04 13:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c24edfd90924"
down_revision: str | Sequence[str] | None = "7e078f080a51"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add 'admin' value to user_role enum and add verification_reason column."""
    # PostgreSQL requires ALTER TYPE to add enum values.
    # The IF NOT EXISTS clause prevents errors on repeated runs.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'admin'")

    op.add_column(
        "professional_profiles",
        sa.Column("verification_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Remove verification_reason column.

    Note: PostgreSQL does not support removing enum values. Removing 'admin'
    from user_role would require recreating the type, which is a destructive
    operation. The downgrade only removes the column added in this migration.
    """
    op.drop_column("professional_profiles", "verification_reason")
