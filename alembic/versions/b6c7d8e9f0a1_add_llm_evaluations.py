"""llm_evaluations: результаты LLM-as-judge для админки (T-039)."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEVEL = postgresql.ENUM(
    "beginner", "club", "gm", name="level", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "llm_evaluations",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("level", _LEVEL, nullable=False),
        sa.Column("accuracy_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("usefulness_score", sa.Numeric(4, 2), nullable=False),
        sa.Column("style_score", sa.Numeric(4, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_llm_evaluations_level_created",
        "llm_evaluations",
        ["level", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_evaluations_level_created", table_name="llm_evaluations"
    )
    op.drop_table("llm_evaluations")
