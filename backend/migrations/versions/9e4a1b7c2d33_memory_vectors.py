"""memory vectors (semantic search)

Revision ID: 9e4a1b7c2d33
Revises: 8d2f3c0e5b21
Create Date: 2026-11-26 01:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e4a1b7c2d33"
down_revision: str | Sequence[str] | None = "8d2f3c0e5b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_vectors",
        sa.Column("memory_id", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
            name=op.f("fk_memory_vectors_memory_id_memories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("memory_id", name=op.f("pk_memory_vectors")),
    )


def downgrade() -> None:
    op.drop_table("memory_vectors")
