"""init schema

Revision ID: b802598c3778
Revises:
Create Date: 2026-10-28 01:32:52.749356

Первая схема ChessAnnotator (SPEC.md §4): users, games, moves, motifs,
annotated_games, retrieval_cache, commentaries, edits.

Правки после autogenerate:

* ``import pgvector`` — тип ``VECTOR(1024)`` рендерится Alembic'ом с
  полным именем модуля;
* ``CREATE EXTENSION IF NOT EXISTS vector`` — расширение должно
  существовать до ``CREATE TABLE annotated_games``; в downgrade
  расширение **не** удаляется (оно может использоваться другими схемами
  той же БД, см. KI-003);
* нативный тип ``level`` создаётся/удаляется явно — иначе после
  ``downgrade base`` остаётся «висячий» тип, и повторный ``upgrade``
  падает на ``type "level" already exists``.
"""

from typing import Sequence, Union

import pgvector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b802598c3778"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024

# create_type=False: тип создаётся и удаляется вручную в upgrade/downgrade.
level_enum = postgresql.ENUM("beginner", "club", "gm", name="level", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    level_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "annotated_games",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("pgn", sa.Text(), nullable=False),
        sa.Column("white", sa.String(length=128), nullable=True),
        sa.Column("black", sa.String(length=128), nullable=True),
        sa.Column("event", sa.String(length=255), nullable=True),
        sa.Column("year", sa.SmallInteger(), nullable=True),
        sa.Column("annotation_text", sa.Text(), nullable=True),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.vector.VECTOR(dim=EMBEDDING_DIM),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_annotated_games")),
    )
    op.create_index(
        "ix_annotated_games_embedding",
        "annotated_games",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "retrieval_cache",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("fen_hash", sa.String(length=64), nullable=False),
        sa.Column("top_k_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_cache")),
    )
    op.create_index(
        "ix_retrieval_cache_fen_hash", "retrieval_cache", ["fen_hash"], unique=False
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("level", level_enum, server_default=sa.text("'club'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "games",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("pgn", sa.Text(), nullable=False),
        sa.Column("eco_code", sa.String(length=3), nullable=True),
        sa.Column(
            "phase_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_games_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_games")),
    )

    op.create_table(
        "commentaries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("game_id", sa.BigInteger(), nullable=False),
        sa.Column("level", level_enum, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column(
            "validated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "human_edited",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name=op.f("fk_commentaries_game_id_games"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commentaries")),
    )
    op.create_index(
        "ix_commentaries_game_id", "commentaries", ["game_id"], unique=False
    )

    op.create_table(
        "moves",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("game_id", sa.BigInteger(), nullable=False),
        sa.Column("ply", sa.Integer(), nullable=False),
        sa.Column("san", sa.String(length=10), nullable=False),
        sa.Column("fen", sa.String(length=120), nullable=False),
        sa.Column("eval_cp", sa.Integer(), nullable=True),
        sa.Column("eval_mate", sa.Integer(), nullable=True),
        sa.Column("best_move_san", sa.String(length=10), nullable=True),
        sa.Column("delta_cp", sa.Integer(), nullable=True),
        sa.Column("classification", sa.String(length=20), nullable=True),
        sa.Column("engine_depth", sa.SmallInteger(), nullable=True),
        sa.Column(
            "multipv", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["game_id"],
            ["games.id"],
            name=op.f("fk_moves_game_id_games"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moves")),
    )
    op.create_index("ix_moves_game_id_ply", "moves", ["game_id", "ply"], unique=False)

    op.create_table(
        "edits",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("commentary_id", sa.BigInteger(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=False),
        sa.Column("editor_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["commentary_id"],
            ["commentaries.id"],
            name=op.f("fk_edits_commentary_id_commentaries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["editor_id"],
            ["users.id"],
            name=op.f("fk_edits_editor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_edits")),
    )
    op.create_index("ix_edits_commentary_id", "edits", ["commentary_id"], unique=False)

    op.create_table(
        "motifs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("move_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "importance",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["move_id"],
            ["moves.id"],
            name=op.f("fk_motifs_move_id_moves"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_motifs")),
    )


def downgrade() -> None:
    op.drop_table("motifs")
    op.drop_index("ix_edits_commentary_id", table_name="edits")
    op.drop_table("edits")
    op.drop_index("ix_moves_game_id_ply", table_name="moves")
    op.drop_table("moves")
    op.drop_index("ix_commentaries_game_id", table_name="commentaries")
    op.drop_table("commentaries")
    op.drop_table("games")
    op.drop_table("users")
    op.drop_index("ix_retrieval_cache_fen_hash", table_name="retrieval_cache")
    op.drop_table("retrieval_cache")
    op.drop_index(
        "ix_annotated_games_embedding",
        table_name="annotated_games",
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("annotated_games")
    # Расширение vector не удаляем: им могут пользоваться другие схемы (KI-003).
    level_enum.drop(op.get_bind(), checkfirst=True)
