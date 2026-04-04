"""F2 part 3: specialty_pricing and consult_quotes tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-04 15:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Placeholder pricing for MVP specialties (values are configurable via admin API).
# Prices in BRL cents: base / min / max
_MVP_PRICING = [
    ("clinico-geral", 14990, 9990, 24990),
    ("pediatria", 16990, 9990, 29990),
    ("psicologia", 19990, 12990, 34990),
    ("psiquiatria", 24990, 14990, 39990),
    ("dermatologia", 19990, 12990, 34990),
    ("endocrinologia", 19990, 12990, 34990),
]


def upgrade() -> None:
    op.create_table(
        "specialty_pricing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "specialty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("specialties.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("base_price_cents", sa.Integer(), nullable=False),
        sa.Column("min_price_cents", sa.Integer(), nullable=False),
        sa.Column("max_price_cents", sa.Integer(), nullable=False),
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
    op.create_index("ix_specialty_pricing_specialty_id", "specialty_pricing", ["specialty_id"])

    op.create_table(
        "consult_quotes",
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
        sa.Column("quoted_price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "expired", "used", name="quote_status"),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_index("ix_consult_quotes_patient_user_id", "consult_quotes", ["patient_user_id"])
    op.create_index("ix_consult_quotes_specialty_id", "consult_quotes", ["specialty_id"])

    # Seed placeholder pricing for the 6 MVP specialties
    import uuid

    conn = op.get_bind()
    for slug, base, min_p, max_p in _MVP_PRICING:
        row = conn.execute(
            sa.text("SELECT id FROM specialties WHERE slug = :slug"),
            {"slug": slug},
        ).fetchone()
        if row is not None:
            conn.execute(
                sa.text(
                    "INSERT INTO specialty_pricing"
                    " (id, specialty_id, base_price_cents, min_price_cents, max_price_cents)"
                    " VALUES (:id, :specialty_id, :base, :min_p, :max_p)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "specialty_id": str(row[0]),
                    "base": base,
                    "min_p": min_p,
                    "max_p": max_p,
                },
            )


def downgrade() -> None:
    op.drop_index("ix_consult_quotes_specialty_id", table_name="consult_quotes")
    op.drop_index("ix_consult_quotes_patient_user_id", table_name="consult_quotes")
    op.drop_table("consult_quotes")
    op.execute("DROP TYPE IF EXISTS quote_status")
    op.drop_index("ix_specialty_pricing_specialty_id", table_name="specialty_pricing")
    op.drop_table("specialty_pricing")
