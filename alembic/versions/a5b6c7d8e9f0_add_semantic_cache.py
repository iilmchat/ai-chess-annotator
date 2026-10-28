"""semantic_cache: LLM-кэш по FEN/уровню/отпечатку промпта (T-032).

`vector(768)` + HNSW: точный поиск по fen_hash и семантический по
косинусному расстоянию. TTL — на уровне приложения (created_at).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEVEL = postgresql.ENUM(
    "beginner", "club", "gm", name="level", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "semantic_cache",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column("fen_hash", sa.String(length=64), nullable=False),
        sa.Column("fen_embedding", Vector(768), nullable=False),
        sa.Column("level", _LEVEL, nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "fen_hash",
            "level",
            "prompt_hash",
            name="uq_semantic_cache_key",
        ),
    )
    op.create_index(
        "ix_semantic_cache_lookup",
        "semantic_cache",
        ["level", "prompt_hash", "created_at"],
    )
    op.create_index(
        "ix_semantic_cache_embedding",
        "semantic_cache",
        ["fen_embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"fen_embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_semantic_cache_embedding", table_name="semantic_cache"
    )
    op.drop_index("ix_semantic_cache_lookup", table_name="semantic_cache")
    op.drop_table("semantic_cache")
