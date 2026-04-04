"""F2 part 1: specialties catalog and professional_specialties link table

Revision ID: a1b2c3d4e5f6
Revises: c24edfd90924
Create Date: 2026-04-04 14:50:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "c24edfd90924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MVP_SPECIALTIES = [
    ("clinico-geral", "Clínico Geral"),
    ("pediatria", "Pediatria"),
    ("psicologia", "Psicologia"),
    ("psiquiatria", "Psiquiatria"),
    ("dermatologia", "Dermatologia"),
    ("endocrinologia", "Endocrinologia"),
]


def upgrade() -> None:
    """Create specialties and professional_specialties tables, then seed MVP data."""
    op.create_table(
        "specialties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
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

    op.create_table(
        "professional_specialties",
        sa.Column(
            "professional_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "specialty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("specialties.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.UniqueConstraint(
            "professional_user_id", "specialty_id", name="uq_professional_specialty"
        ),
    )

    # Seed MVP specialties
    conn = op.get_bind()
    import uuid

    for slug, name in _MVP_SPECIALTIES:
        conn.execute(
            sa.text(
                "INSERT INTO specialties (id, slug, name, active) VALUES (:id, :slug, :name, true)"
            ),
            {"id": str(uuid.uuid4()), "slug": slug, "name": name},
        )


def downgrade() -> None:
    """Drop professional_specialties and specialties tables."""
    op.drop_table("professional_specialties")
    op.drop_table("specialties")
