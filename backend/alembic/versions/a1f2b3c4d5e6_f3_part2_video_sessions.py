"""f3_part2_video_sessions

Revision ID: a1f2b3c4d5e6
Revises: f3a4b5c6d7e8
Create Date: 2026-04-06 14:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f2b3c4d5e6"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "consult_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consult_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("room_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="TWILIO"),
        sa.Column(
            "status",
            sa.Enum(
                "CREATING",
                "READY",
                "ACTIVE",
                "ENDED",
                "ERROR",
                name="video_session_status",
            ),
            nullable=False,
            server_default="CREATING",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_video_sessions_consult_request_id",
        "video_sessions",
        ["consult_request_id"],
    )
    op.create_unique_constraint(
        "uq_video_sessions_consult_request_id",
        "video_sessions",
        ["consult_request_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_video_sessions_consult_request_id", "video_sessions", type_="unique"
    )
    op.drop_index("ix_video_sessions_consult_request_id", table_name="video_sessions")
    op.drop_table("video_sessions")
    op.execute("DROP TYPE IF EXISTS video_session_status")
