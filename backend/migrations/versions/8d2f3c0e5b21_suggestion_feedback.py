"""suggestion feedback (thumbs up/down)

Revision ID: 8d2f3c0e5b21
Revises: 7c1e2b9d4a10
Create Date: 2026-11-26 00:20:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d2f3c0e5b21"
down_revision: str | Sequence[str] | None = "7c1e2b9d4a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suggestion_feedback",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("scenario", sa.String(length=16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_suggestion_feedback_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suggestion_feedback")),
    )
    op.create_index(
        "ix_feedback_user_question",
        "suggestion_feedback",
        ["user_id", "question_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_user_question", table_name="suggestion_feedback")
    op.drop_table("suggestion_feedback")
