"""ChessAnnotator ORM models.

Источник истины по схеме — `docs/development/SPEC.md` §4.
Все таблицы создаются миграцией `alembic/versions/*_init_schema.py`.

Соглашения:

* первичные ключи — ``BIGINT GENERATED ALWAYS AS IDENTITY``;
* временные метки — ``TIMESTAMPTZ`` со ``server_default=now()``;
* перечисления — нативные PostgreSQL-типы (``level``);
* гибкие структуры (разбивка по фазам, MultiPV, retrieval top-k) — ``JSONB``;
* эмбеддинги — ``pgvector.sqlalchemy.Vector(1024)`` + ivfflat-индекс.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

__all__ = [
    "AnnotatedGame",
    "Commentary",
    "Edit",
    "Game",
    "Level",
    "Motif",
    "Move",
    "RetrievalCache",
    "User",
    "level_enum",
]

EMBEDDING_DIM = 1024


class Level(enum.StrEnum):
    """Уровень ассистента (SPEC.md §3)."""

    beginner = "beginner"
    club = "club"
    gm = "gm"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Хранить в БД значения (``beginner``), а не имена членов перечисления."""
    return [str(member.value) for member in enum_cls]


level_enum = Enum(Level, name="level", values_callable=_enum_values)


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    """Пользователь и его уровень по умолчанию."""

    __tablename__ = "users"

    id: Mapped[int] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    level: Mapped[Level] = mapped_column(
        level_enum, nullable=False, server_default=sql_text("'club'")
    )
    created_at: Mapped[datetime] = _created_at()

    games: Mapped[list[Game]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Game(Base):
    """Загруженная партия в PGN."""

    __tablename__ = "games"

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    pgn: Mapped[str] = mapped_column(Text, nullable=False)
    eco_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Формат задаёт app.ingestion.models.PhaseBreakdown (T-007):
    # {"opening_end_ply": 30, "endgame_start_ply": null}
    phase_breakdown: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    user: Mapped[User | None] = relationship(back_populates="games")
    moves: Mapped[list[Move]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Move.ply",
    )
    commentaries: Mapped[list[Commentary]] = relationship(
        back_populates="game", cascade="all, delete-orphan", passive_deletes=True
    )


class Move(Base):
    """Оценка движка для одного полухода."""

    __tablename__ = "moves"
    __table_args__ = (Index("ix_moves_game_id_ply", "game_id", "ply"),)

    id: Mapped[int] = _pk()
    game_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    ply: Mapped[int] = mapped_column(Integer, nullable=False)
    san: Mapped[str] = mapped_column(String(10), nullable=False)
    fen: Mapped[str] = mapped_column(String(120), nullable=False)
    eval_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eval_mate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_move_san: Mapped[str | None] = mapped_column(String(10), nullable=True)
    delta_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # blunder | mistake | inaccuracy | good | best | brilliant (T-008)
    classification: Mapped[str | None] = mapped_column(String(20), nullable=True)
    engine_depth: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # [{"rank": 1, "move": "Nf3", "eval_cp": 24, "pv": ["Nf3", "d5"]}, ...]
    multipv: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    game: Mapped[Game] = relationship(back_populates="moves")
    motifs: Mapped[list[Motif]] = relationship(
        back_populates="move", cascade="all, delete-orphan", passive_deletes=True
    )


class Motif(Base):
    """Мотив, привязанный к ходу (T-010)."""

    __tablename__ = "motifs"

    id: Mapped[int] = _pk()
    move_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("moves.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=sql_text("0")
    )

    move: Mapped[Move] = relationship(back_populates="motifs")


class AnnotatedGame(Base):
    """Аннотированная партия для RAG (T-017)."""

    __tablename__ = "annotated_games"
    __table_args__ = (
        Index(
            "ix_annotated_games_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = _pk()
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    pgn: Mapped[str] = mapped_column(Text, nullable=False)
    white: Mapped[str | None] = mapped_column(String(128), nullable=True)
    black: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event: Mapped[str | None] = mapped_column(String(255), nullable=True)
    year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    annotation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )


class RetrievalCache(Base):
    """Кэш retrieval по хэшу позиции (T-018, T-108)."""

    __tablename__ = "retrieval_cache"
    __table_args__ = (Index("ix_retrieval_cache_fen_hash", "fen_hash"),)

    id: Mapped[int] = _pk()
    fen_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    top_k_ids: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Commentary(Base):
    """Сгенерированный комментарий под конкретный уровень (T-016)."""

    __tablename__ = "commentaries"
    __table_args__ = (Index("ix_commentaries_game_id", "game_id"),)

    id: Mapped[int] = _pk()
    game_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[Level] = mapped_column(level_enum, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    validated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    human_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    created_at: Mapped[datetime] = _created_at()

    game: Mapped[Game] = relationship(back_populates="commentaries")
    edits: Mapped[list[Edit]] = relationship(
        back_populates="commentary", cascade="all, delete-orphan", passive_deletes=True
    )


class Edit(Base):
    """Правка комментария человеком — датасет для дообучения (T-021/T-104)."""

    __tablename__ = "edits"
    __table_args__ = (Index("ix_edits_commentary_id", "commentary_id"),)

    id: Mapped[int] = _pk()
    commentary_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("commentaries.id", ondelete="CASCADE"), nullable=False
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_text: Mapped[str] = mapped_column(Text, nullable=False)
    editor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    commentary: Mapped[Commentary] = relationship(back_populates="edits")
    editor: Mapped[User | None] = relationship()
