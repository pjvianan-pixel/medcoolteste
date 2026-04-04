"""initial: users, patient_profiles, professional_profiles

Revision ID: 7e078f080a51
Revises: 
Create Date: 2026-04-04 12:40:02.468586

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e078f080a51"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial tables: users, patient_profiles, professional_profiles."""
    # Enum types
    user_role = postgresql.ENUM("patient", "professional", name="user_role")
    user_role.create(op.get_bind())

    verification_status = postgresql.ENUM(
        "pending", "approved", "rejected", name="verification_status"
    )
    verification_status.create(op.get_bind())

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.Enum("patient", "professional", name="user_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "patient_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("cpf", sa.String(length=14), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cpf"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "professional_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("crm", sa.String(length=50), nullable=False),
        sa.Column("specialty", sa.String(length=100), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column(
            "is_available", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "status_verificacao",
            sa.Enum("pending", "approved", "rejected", name="verification_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crm"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    """Drop initial tables and enum types."""
    op.drop_table("professional_profiles")
    op.drop_table("patient_profiles")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS verification_status")
    op.execute("DROP TYPE IF EXISTS user_role")

