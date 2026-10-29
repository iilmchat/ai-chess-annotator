"""Схема БД: метаданные моделей и поведение ORM (T-004).

Тесты делятся на два уровня:

* чистые unit-тесты метаданных (не требуют БД) — проверяют, что в
  ``Base.metadata`` есть все таблицы SPEC.md §4, индексы и размерность
  эмбеддинга;
* интеграционные тесты на живой PostgreSQL — проверяют server_default'ы
  и каскадное удаление. Если БД недоступна, они помечаются ``skipped``
  и не роняют локальный прогон (в CI БД поднята сервисом).
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.base import Base
from app.db.models import (
    EMBEDDING_DIM,
    AnnotatedGame,
    Commentary,
    Edit,
    Game,
    Level,
    Motif,
    Move,
    RetrievalCache,
    User,
)

SPEC_TABLES = {
    "annotated_games",
    "audit_logs",
    "commentaries",
    "edits",
    "engine_cache",
    "export_jobs",
    "games",
    "llm_calls",
    "llm_evaluations",
    "motifs",
    "moves",
    "prompts",
    "retrieval_cache",
    "semantic_cache",
    "usage_daily",
    "users",
}

PGN = '[Event "Test"]\n\n1. e4 e5 2. Nf3 Nc6 1/2-1/2\n'


def test_metadata_contains_exactly_spec_tables() -> None:
    assert set(Base.metadata.tables) == SPEC_TABLES


def test_users_have_email_verification_flag() -> None:
    """KI-041: `users.is_verified` NOT NULL, legacy-строки — true."""
    users = Base.metadata.tables["users"]
    column = users.c.is_verified
    assert column.nullable is False
    assert column.server_default is not None


def test_engine_cache_table_has_unique_key_and_ttl_index() -> None:
    """T-108: ключ кэша — позиция + версия движка + depth/multipv."""
    table = Base.metadata.tables["engine_cache"]
    assert table.c.fen_hash.nullable is False
    assert table.c.engine_version.nullable is False
    assert table.c.depth.nullable is False
    assert table.c.multipv.nullable is False
    names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }
    assert "uq_engine_cache_key" in names
    indexes = {index.name for index in table.indexes}
    assert "ix_engine_cache_created" in indexes


def test_export_jobs_table_has_status_and_integrity_fields() -> None:
    """KI-050: фоновая выгрузка — статус, снимок фильтров, файл, sha256."""
    table = Base.metadata.tables["export_jobs"]
    assert table.c.status.nullable is False
    assert table.c.actor.nullable is False
    assert table.c.filters.nullable is False
    assert table.c.rows_exported.nullable is False
    indexes = {index.name for index in table.indexes}
    assert "ix_export_jobs_created" in indexes
    assert "ix_export_jobs_status_created" in indexes
    checks = {
        str(constraint.name)
        for constraint in table.constraints
        if constraint.name
    }
    assert any("status" in name for name in checks)


def test_llm_calls_table_has_trace_and_indexes() -> None:
    """T-030: durable-история LLM — обязательные поля и индексы."""
    calls = Base.metadata.tables["llm_calls"]
    assert calls.c.trace_id.nullable is False
    assert calls.c.span_id.nullable is False
    assert calls.c.latency_ms.nullable is False
    assert calls.c.status.nullable is False
    unique = {
        constraint.name for constraint in calls.constraints if constraint.name
    }
    assert "uq_llm_calls_trace_span" in unique
    indexes = {index.name for index in calls.indexes}
    assert {
        "ix_llm_calls_created_brin",
        "ix_llm_calls_model_created",
        "ix_llm_calls_level_created",
    } <= indexes


def test_usage_daily_table_has_unique_and_indexes() -> None:
    """T-031b: durable-агрегат — уникальность субъекта/дня и индексы."""
    table = Base.metadata.tables["usage_daily"]
    assert table.c.subject_type.nullable is False
    assert table.c.subject_key.nullable is False
    assert table.c.day.nullable is False
    assert table.c.tokens_in.nullable is False
    unique = {
        constraint.name for constraint in table.constraints if constraint.name
    }
    assert "uq_usage_daily_subject_day" in unique
    indexes = {index.name for index in table.indexes}
    assert {
        "ix_usage_daily_day_subject",
        "ix_usage_daily_subject_day",
    } <= indexes


def test_moves_index_and_embedding_dimension() -> None:
    moves = Base.metadata.tables["moves"]
    indexes = {index.name for index in moves.indexes}
    assert "ix_moves_game_id_ply" in indexes

    embedding = Base.metadata.tables["annotated_games"].c.embedding
    assert getattr(embedding.type, "dim", None) == EMBEDDING_DIM == 768

    level_column = Base.metadata.tables["users"].c.level
    assert level_column.nullable is False


def test_level_enum_values() -> None:
    assert [level.value for level in Level] == ["beginner", "club", "gm"]
    assert Level("gm") is Level.gm


def test_llm_evaluations_table_has_scores() -> None:
    evaluations = Base.metadata.tables["llm_evaluations"]
    assert evaluations.c.model.nullable is False
    assert evaluations.c.accuracy_score.nullable is False
    assert evaluations.c.usefulness_score.nullable is False
    assert evaluations.c.style_score.nullable is False


def test_semantic_cache_table_has_vector_and_key() -> None:
    cache = Base.metadata.tables["semantic_cache"]
    assert getattr(cache.c.fen_embedding.type, "dim", None) == 768
    assert cache.c.prompt_hash.nullable is False


def test_audit_logs_table_has_hash_columns() -> None:
    audit = Base.metadata.tables["audit_logs"]
    assert audit.c.prev_hash.nullable is False
    assert audit.c.current_hash.nullable is False
    assert audit.c.details.nullable is False


def test_prompts_table_has_unique_level_and_version() -> None:
    prompts = Base.metadata.tables["prompts"]
    unique = {
        constraint.name
        for constraint in prompts.constraints
        if constraint.name == "uq_prompts_level"
    }
    assert unique == {"uq_prompts_level"}
    assert prompts.c.version.nullable is False
    assert prompts.c.updated_at.nullable is False


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL недоступен: {exc}")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def test_server_defaults_and_cascade_delete(session: AsyncSession) -> None:
    user = User(email=f"user-{uuid4().hex}@example.com")
    game = Game(pgn=PGN, user=user)
    session.add(game)
    await session.flush()

    assert user.id is not None
    # Server defaults читаем отдельным запросом: Session.refresh() «просрочил»
    # бы связанные объекты и следующий доступ к game.id сходил бы в БД из
    # синхронного контекста (MissingGreenlet).
    level = await session.scalar(select(User.level).where(User.id == user.id))
    created_at = await session.scalar(select(User.created_at).where(User.id == user.id))
    assert level is Level.club
    assert created_at is not None

    move = Move(game_id=game.id, ply=1, san="e4", fen="8/8/8/8/8/8/8/8 w - - 0 1")
    session.add(move)
    await session.flush()

    motif = Motif(move_id=move.id, type="center_control")
    commentary = Commentary(
        game_id=game.id, level=Level.gm, text="Текст", model="stub-model"
    )
    session.add_all([motif, commentary])
    await session.flush()

    validated = await session.scalar(
        select(Commentary.validated).where(Commentary.id == commentary.id)
    )
    human_edited = await session.scalar(
        select(Commentary.human_edited).where(Commentary.id == commentary.id)
    )
    assert validated is False
    assert human_edited is False

    edit = Edit(
        commentary_id=commentary.id,
        original_text="a",
        edited_text="b",
        editor_id=user.id,
    )
    session.add_all(
        [
            edit,
            RetrievalCache(fen_hash="0" * 64, top_k_ids=[game.id]),
            AnnotatedGame(
                source="test",
                pgn=PGN,
                white="White",
                black="Black",
                year=2026,
                embedding=[0.0] * EMBEDDING_DIM,
            ),
        ]
    )
    await session.flush()

    game_id, move_id = game.id, move.id
    await session.delete(game)
    await session.flush()

    move_count = await session.scalar(
        select(func.count()).select_from(Move).where(Move.game_id == game_id)
    )
    motif_count = await session.scalar(
        select(func.count()).select_from(Motif).where(Motif.move_id == move_id)
    )
    commentary_count = await session.scalar(
        select(func.count())
        .select_from(Commentary)
        .where(Commentary.game_id == game_id)
    )
    assert move_count == 0
    assert motif_count == 0
    assert commentary_count == 0
