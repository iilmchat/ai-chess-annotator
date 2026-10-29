"""ChessAnnotator ORM models.

Источник истины по схеме — `docs/development/SPEC.md` §4.
Все таблицы создаются миграцией `alembic/versions/*_init_schema.py`.

Соглашения:

* первичные ключи — ``BIGINT GENERATED ALWAYS AS IDENTITY``;
* временные метки — ``TIMESTAMPTZ`` со ``server_default=now()``;
* перечисления — нативные PostgreSQL-типы (``level``);
* гибкие структуры (разбивка по фазам, MultiPV, retrieval top-k) — ``JSONB``;
* эмбеддинги — ``pgvector.sqlalchemy.Vector(768)`` + ivfflat-индекс.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

__all__ = [
    "AdminRole",
    "AnnotatedGame",
    "AuditLog",
    "Commentary",
    "Edit",
    "EngineCacheEntry",
    "ExportJob",
    "ExportJobStatus",
    "Game",
    "Level",
    "LlmCall",
    "LlmEvaluation",
    "Motif",
    "Move",
    "Prompt",
    "RetrievalCache",
    "SemanticCache",
    "UsageDaily",
    "User",
    "admin_role_enum",
    "level_enum",
]

EMBEDDING_DIM = 768


class Level(enum.StrEnum):
    """Уровень ассистента (SPEC.md §3)."""

    beginner = "beginner"
    club = "club"
    gm = "gm"


class AdminRole(enum.StrEnum):
    """Роль пользователя (T-028, DESIGN_ADMIN_AND_OPS §4.1)."""

    user = "user"
    admin = "admin"
    auditor = "auditor"


class ExportJobStatus(enum.StrEnum):
    """Статус фоновой выгрузки датасета (KI-050, итерация 31)."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Хранить в БД значения (``beginner``), а не имена членов перечисления."""
    return [str(member.value) for member in enum_cls]


level_enum = Enum(Level, name="level", values_callable=_enum_values)
admin_role_enum = Enum(AdminRole, name="admin_role", values_callable=_enum_values)


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    """Пользователь, его уровень и права (T-028)."""

    __tablename__ = "users"
    __table_args__ = (
        # Частичный индекс: роли, отличные от user, встречаются редко.
        Index(
            "ix_users_role",
            "role",
            postgresql_where=sql_text("role <> 'user'"),
        ),
    )

    id: Mapped[int] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    level: Mapped[Level] = mapped_column(
        level_enum, nullable=False, server_default=sql_text("'club'")
    )
    role: Mapped[AdminRole] = mapped_column(
        admin_role_enum, nullable=False, server_default=sql_text("'user'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("true")
    )
    #: bcrypt-хеш; NULL — вход по паролю не настроен (SSO/служебный аккаунт).
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    games: Mapped[list[Game]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Game(Base):
    """Загруженная партия в PGN."""

    __tablename__ = "games"
    __table_args__ = (
        # История «мои партии»: фильтр по владельцу + сортировка по дате.
        Index("ix_games_user_id_created", "user_id", "created_at"),
    )

    id: Mapped[int] = _pk()
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    pgn: Mapped[str] = mapped_column(Text, nullable=False)
    eco_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Уровень, под который выполнялся анализ (T-031: GET /v1/games/{id}).
    analysis_level: Mapped[Level] = mapped_column(
        level_enum, nullable=False, server_default=sql_text("'club'")
    )
    # Время анализа в мс (для повторной выдачи без пересчёта).
    analysis_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    __table_args__ = (
        Index("ix_commentaries_game_id", "game_id"),
        Index("ix_commentaries_user_id", "user_id"),
    )

    id: Mapped[int] = _pk()
    game_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    # Владелец комментария (T-028): NULL — legacy-запись до JWT.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
    __table_args__ = (
        Index("ix_edits_commentary_id", "commentary_id"),
        Index("ix_edits_user_id", "user_id"),
    )

    id: Mapped[int] = _pk()
    commentary_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("commentaries.id", ondelete="CASCADE"), nullable=False
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Автор правки (T-028): дублирует editor_id для явного фильтра по владельцу.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    editor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created_at()

    commentary: Mapped[Commentary] = relationship(back_populates="edits")
    # Две FK на users (user_id + editor_id) — join только по editor_id.
    editor: Mapped[User | None] = relationship(foreign_keys=[editor_id])


class Prompt(Base):
    """Системный промпт уровня в БД (T-029, итерация 17).

    Одна строка на уровень; `version` инкрементируется при правке в
    админке, `updated_at` — серверное время. Полная история версий и
    rollback — следующий шаг T-029 (см. отчёт итерации 17).
    """

    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("level", name="uq_prompts_level"),
        CheckConstraint("length(content) > 0", name="ck_prompts_content_nonempty"),
    )

    id: Mapped[int] = _pk()
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LlmEvaluation(Base):
    """Результат LLM-as-judge для админ-дашборда (T-039, итерация 22)."""

    __tablename__ = "llm_evaluations"
    __table_args__ = (
        Index(
            "ix_llm_evaluations_level_created",
            "level",
            "created_at",
        ),
    )

    id: Mapped[int] = _pk()
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[Level] = mapped_column(level_enum, nullable=False)
    accuracy_score: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False
    )
    usefulness_score: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False
    )
    style_score: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class SemanticCache(Base):
    """Кэш текстов LLM по позиции/уровню/отпечатку промпта (T-032).

    `fen_hash` — точный ключ, `fen_embedding` — семантический поиск;
    `prompt_hash` не даёт вернуть ответ на другой вопрос/стиль/версию
    системного промпта. TTL проверяется по `created_at`.
    """

    __tablename__ = "semantic_cache"
    __table_args__ = (
        UniqueConstraint(
            "fen_hash",
            "level",
            "prompt_hash",
            name="uq_semantic_cache_key",
        ),
        Index(
            "ix_semantic_cache_lookup",
            "level",
            "prompt_hash",
            "created_at",
        ),
        Index(
            "ix_semantic_cache_embedding",
            "fen_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"fen_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = _pk()
    fen_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fen_embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=False
    )
    level: Mapped[Level] = mapped_column(level_enum, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    """Append-only аудит действий с hash-chain (T-035, итерация 18).

    `current_hash` считается по `prev_hash + timestamp + actor + action +
    target_id + canonical_json(details)`. Триггер в миграции запрещает
    UPDATE/DELETE; разрыв цепочки ловится `verify_audit_chain`.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_action_timestamp", "action", "timestamp"),
    )

    id: Mapped[int] = _pk()
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class LlmCall(Base):
    """Durable-история вызовов LLM (T-030, DESIGN_ADMIN_AND_OPS §4.4).

    Пишется best-effort **после** ответа LLM (recorder `app.llm.calls`):
    один ряд — один вызов провайдера. Тексты промптов/ответов сюда не
    попадают (privacy): только операция, модель, токены, стоимость и
    статус. `trace_id` связывает вызов с HTTP-запросом (`X-Trace-Id`) и
    OTel-трейсом; `span_id` — синтетический идентификатор попытки вызова
    (16 hex), не равен OTel span id и нужен для UNIQUE(trace, span) при
    retry/нескольких вызовах в одном запросе.

    `level` — нативный enum `level`, NULL для вызовов без уровня
    (warmup/классификатор). `UNIQUE (trace_id, span_id)` защищает от
    дублей при ретраях доставки; каждая попытка LLM получает свой
    `span_id`.
    """

    __tablename__ = "llm_calls"
    __table_args__ = (
        UniqueConstraint("trace_id", "span_id", name="uq_llm_calls_trace_span"),
        # BRIN дешёв для append-only истории; DESC-индексы — под выборки
        # «последние вызовы по модели/уровню» в админ-дашборде.
        Index(
            "ix_llm_calls_created_brin",
            "created_at",
            postgresql_using="brin",
        ),
        Index(
            "ix_llm_calls_model_created",
            "model",
            sql_text("created_at DESC"),
        ),
        Index(
            "ix_llm_calls_level_created",
            "level",
            sql_text("created_at DESC"),
        ),
        CheckConstraint("tokens_in >= 0", name="ck_llm_calls_tokens_in_nonnegative"),
        CheckConstraint(
            "tokens_out >= 0", name="ck_llm_calls_tokens_out_nonnegative"
        ),
        CheckConstraint(
            "cost_usd >= 0", name="ck_llm_calls_cost_usd_nonnegative"
        ),
        CheckConstraint(
            "latency_ms >= 0", name="ck_llm_calls_latency_ms_nonnegative"
        ),
    )

    id: Mapped[int] = _pk()
    trace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    span_id: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    game_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("games.id", ondelete="SET NULL"),
        nullable=True,
    )
    commentary_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("commentaries.id", ondelete="SET NULL"),
        nullable=True,
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[Level | None] = mapped_column(level_enum, nullable=True)
    prompt_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    tokens_out: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=sql_text("0")
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()


class EngineCacheEntry(Base):
    """Кэш результатов Stockfish по позиции (T-108, итерация 30).

    Ключ — позиция + версия движка + параметры поиска: `fen_hash` —
    sha256 канонического FEN (без номера хода, чтобы одна и та же позиция,
    достигнутая в разное время, попадала в один ключ), `engine_version` —
    UCI `id name` бинарника, `depth`/`multipv` — параметры поиска уровня.
    Без версии и глубины оценка `beginner` (depth 12, MultiPV 1) могла бы
    быть отдана уровню `gm` (depth 28, MultiPV 5), а после обновления
    Stockfish — переиспользоваться неверная оценка.

    `lines` — JSONB-список `EngineLine` (multipv/score_cp|mate/move_uci/pv);
    `created_at` + `ENGINE_CACHE_TTL_DAYS` определяют срок жизни, физическую
    чистку делает `EngineCache.cleanup_expired` при старте приложения.
    """

    __tablename__ = "engine_cache"
    __table_args__ = (
        UniqueConstraint(
            "fen_hash",
            "engine_version",
            "depth",
            "multipv",
            name="uq_engine_cache_key",
        ),
        Index("ix_engine_cache_created", "created_at"),
        CheckConstraint("depth >= 1", name="ck_engine_cache_depth_positive"),
        CheckConstraint(
            "multipv >= 1", name="ck_engine_cache_multipv_positive"
        ),
    )

    id: Mapped[int] = _pk()
    fen_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    multipv: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


class ExportJob(Base):
    """Фоновая выгрузка датасета LoRA (KI-050, итерация 31).

    Большие выгрузки JSONL не должны держать браузер на streaming-запросе:
    `POST /v1/admin/exports/jobs` создаёт строку `queued`, BackgroundTasks
    генерирует файл в `data/exports/` (вне web-root), статус меняется на
    ``running`` → ``done``/``failed``. Скачивание — отдельным запросом по
    готовому файлу.

    `filters` — снимок query-фильтров (`date_from`, `date_to`, `level`,
    `human_edited`, `schema_version`), `actor` — кто запустил
    (``jwt:<email>`` / ``session:<username>``), `sha256`/`file_size` —
    контроль целостности файла, `rows_exported` — сколько строк реально
    записано (v2 может пропускать проблемные записи).
    """

    __tablename__ = "export_jobs"
    __table_args__ = (
        Index("ix_export_jobs_status_created", "status", "created_at"),
        Index("ix_export_jobs_created", "created_at"),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name="ck_export_jobs_status",
        ),
        CheckConstraint(
            "rows_exported >= 0",
            name="ck_export_jobs_rows_exported_nonnegative",
        ),
        CheckConstraint(
            "file_size >= 0",
            name="ck_export_jobs_file_size_nonnegative",
        ),
    )

    id: Mapped[int] = _pk()
    #: queued | running | done | failed (см. `ExportJobStatus`).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sql_text("'queued'")
    )
    actor: Mapped[str] = mapped_column(String(320), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    #: Путь к готовому JSONL (NULL, пока джоба не завершилась).
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rows_exported: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UsageDaily(Base):
    """Durable-агрегат расхода токенов за день (T-031b, DESIGN §4.5).

    Субъект — `user:<id>` или `ip:<ip>` (та же схема, что у Redis-счётчика
    `usage:{day}:{subject}`): `subject_type`/`subject_key` разложены на две
    колонки, чтобы агрегировать и индексировать без разбора строк.

    Пишется best-effort из `app.finops.record_token_usage` (upsert
    `ON CONFLICT DO UPDATE`: токены/стоимость прибавляются, `requests + 1`).
    Redis остаётся горячим счётчиком для 429; `usage_daily` — durable-история
    для FinOps-отчётов и fallback в `/v1/users/me/usage`, когда Redis
    недоступен или пуст.
    """

    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "subject_type",
            "subject_key",
            "day",
            name="uq_usage_daily_subject_day",
        ),
        # Оба порядка нужны: fallback-чтение — по субъекту и дню, отчёты
        # и retention — по дню и типу субъекта.
        Index(
            "ix_usage_daily_day_subject",
            "day",
            "subject_type",
        ),
        Index(
            "ix_usage_daily_subject_day",
            "subject_type",
            "subject_key",
            "day",
        ),
        CheckConstraint(
            "tokens_in >= 0", name="ck_usage_daily_tokens_in_nonnegative"
        ),
        CheckConstraint(
            "tokens_out >= 0", name="ck_usage_daily_tokens_out_nonnegative"
        ),
        CheckConstraint(
            "cost_usd >= 0", name="ck_usage_daily_cost_usd_nonnegative"
        ),
        CheckConstraint(
            "requests >= 0", name="ck_usage_daily_requests_nonnegative"
        ),
    )

    id: Mapped[int] = _pk()
    #: `user` | `ip` (varchar(8) по DESIGN).
    subject_type: Mapped[str] = mapped_column(String(8), nullable=False)
    #: id пользователя или IP (до 64 символов).
    subject_key: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    tokens_in: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=sql_text("0")
    )
    tokens_out: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=sql_text("0")
    )
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=sql_text("0")
    )
    requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


