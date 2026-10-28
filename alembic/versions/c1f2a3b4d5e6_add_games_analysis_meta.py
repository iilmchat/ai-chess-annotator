"""games: analysis_level и analysis_time_ms для GET /v1/games/{id}.

Нужны, чтобы отдавать сохранённый анализ той же структуры, что
`POST /v1/analyze`, без повторного запуска Stockfish.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1f2a3b4d5e6"
down_revision: str | Sequence[str] | None = "b802598c3778"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    level_enum = postgresql.ENUM(
        "beginner", "club", "gm", name="level", create_type=False
    )
    op.add_column(
        "games",
        sa.Column(
            "analysis_level",
            level_enum,
            nullable=False,
            server_default=sa.text("'club'"),
        ),
    )
    op.add_column(
        "games",
        sa.Column("analysis_time_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("games", "analysis_time_ms")
    op.drop_column("games", "analysis_level")
