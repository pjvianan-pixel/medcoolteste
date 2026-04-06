"""f3_part1_chat_messages

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-04-06 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "consult_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consult_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "receiver_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_role",
            sa.Enum("PATIENT", "PROFESSIONAL", name="chat_sender_role"),
            nullable=False,
        ),
        sa.Column(
            "message_type",
            sa.Enum("TEXT", name="chat_message_type"),
            nullable=False,
            server_default="TEXT",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Individual column indices (FK columns)
    op.create_index(
        "ix_chat_messages_consult_request_id",
        "chat_messages",
        ["consult_request_id"],
    )
    op.create_index(
        "ix_chat_messages_sender_user_id",
        "chat_messages",
        ["sender_user_id"],
    )
    op.create_index(
        "ix_chat_messages_receiver_user_id",
        "chat_messages",
        ["receiver_user_id"],
    )
    # Composite index for efficient history queries ordered by time
    op.create_index(
        "ix_chat_messages_consult_sent",
        "chat_messages",
        ["consult_request_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_consult_sent", table_name="chat_messages")
    op.drop_index("ix_chat_messages_receiver_user_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_sender_user_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_consult_request_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.execute("DROP TYPE IF EXISTS chat_sender_role")
    op.execute("DROP TYPE IF EXISTS chat_message_type")
