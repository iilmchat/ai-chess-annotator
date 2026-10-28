"""annotated_games.embedding: 1024 → 768 под LM Studio nomic-embed.

Старые 1024-мерные векторы не конвертируются (разные пространства),
поэтому колонка пересоздаётся, а записи переиндексируются скриптом
`scripts/reembed_annotated.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1f2a3b4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_embedding_index() -> None:
    op.create_index(
        "ix_annotated_games_embedding",
        "annotated_games",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def upgrade() -> None:
    op.drop_index("ix_annotated_games_embedding", table_name="annotated_games")
    op.add_column(
        "annotated_games",
        sa.Column("embedding_768", Vector(768), nullable=True),
    )
    op.drop_column("annotated_games", "embedding")
    op.alter_column(
        "annotated_games", "embedding_768", new_column_name="embedding"
    )
    _create_embedding_index()


def downgrade() -> None:
    op.drop_index("ix_annotated_games_embedding", table_name="annotated_games")
    op.add_column(
        "annotated_games",
        sa.Column("embedding_1024", Vector(1024), nullable=True),
    )
    op.drop_column("annotated_games", "embedding")
    op.alter_column(
        "annotated_games", "embedding_1024", new_column_name="embedding"
    )
    _create_embedding_index()
