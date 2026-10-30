"""Интеграционные тесты семантической валидации комментария (KI-023).

Проверяется сквозной сценарий `generate_commentary` с подставной LLM и
реальной БД: retry при выдуманных фигурах, вырезание offending-предложений
на последней попытке и отключение проверки флагом
`SEMANTIC_VALIDATION_ENABLED`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.commentary import generate_commentary
from app.config import get_settings
from app.db.models import Game, Move
from app.errors import LLMValidationError
from app.ingestion import parse_pgn
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage

SHORT_PGN = "1. d4 Nf6 2. c4 e6 1/2-1/2"

#: Легальные ходы, но слон на e6 выдуман (в партии там пешка).
HALLUCINATED_TEXT = "После d4 и Nf6 слон на e6 занял сильную позицию."
#: Легальные ходы и никаких утверждений о фигурах на полях.
LEGAL_TEXT = "Партия началась с хода d4, затем последовало Nf6."
#: Нелегальный ход — прежний сценарий SAN-валидатора.
ILLEGAL_TEXT = "Белые сыграли Qxf7 и выиграли партию."
#: Ход + выдуманная фигура в одном ответе.
MIXED_TEXT = "Белые сыграли Qxf7, а слон на e6 был активен."
#: Первое предложение невалидно, второе — корректно.
STRIPPABLE_TEXT = "Слон на e6 был активен. Партия началась с хода d4."


class ScriptedLLM(BaseLLMClient):
    """LLM, отдающая заранее заданные ответы по очереди."""

    def __init__(self, texts: list[str]) -> None:
        super().__init__(model="scripted-model")
        self._texts = list(texts)
        self.prompts: list[str] = []
        self.system_prompts: list[str | None] = []

    @property
    def provider(self) -> str:
        return "scripted"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        result_text = self._texts.pop(0) if len(self._texts) > 1 else self._texts[0]
        return LLMResult(
            text=result_text,
            usage=LLMUsage(
                provider="scripted",
                model="scripted-model",
                tokens_in=100,
                tokens_out=50,
            ),
        )


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен")


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def _seed_game(db_session: AsyncSession, pgn: str = SHORT_PGN) -> Game:
    parsed = parse_pgn(pgn)
    game = Game(
        pgn=pgn,
        eco_code=parsed.eco_code,
        phase_breakdown=parsed.phase_breakdown.model_dump(),
    )
    db_session.add(game)
    await db_session.flush()
    for move in parsed.moves:
        db_session.add(
            Move(
                game_id=game.id,
                ply=move.ply,
                san=move.san,
                fen=move.fen,
                eval_cp=20,
                eval_mate=None,
                best_move_san=move.san,
                delta_cp=0,
                classification="good",
                engine_depth=12,
            )
        )
    await db_session.flush()
    return game


# --- retry на семантической ошибке ---------------------------------------


async def test_semantic_hallucination_triggers_retry(
    session: AsyncSession,
) -> None:
    game = await _seed_game(session)
    llm = ScriptedLLM([HALLUCINATED_TEXT, LEGAL_TEXT])

    result = await generate_commentary(session, game.id, "club", client=llm)

    assert result.attempts == 2
    assert result.text == LEGAL_TEXT
    assert result.validated is True
    assert len(llm.prompts) == 2
    # Retry-подсказка содержит проблему и требование переписать.
    assert "слон на e6" in llm.prompts[1]
    assert "ВНИМАНИЕ" in llm.prompts[1]
    assert "Перепиши" in llm.prompts[1]


async def test_clean_text_without_claims_needs_single_attempt(
    session: AsyncSession,
) -> None:
    game = await _seed_game(session)
    llm = ScriptedLLM([LEGAL_TEXT])

    result = await generate_commentary(session, game.id, "club", client=llm)

    assert result.attempts == 1
    assert result.text == LEGAL_TEXT
    assert len(llm.prompts) == 1


# --- последняя попытка: вырезание вместо 502 ------------------------------


async def test_last_attempt_strips_hallucinated_sentence(
    session: AsyncSession,
) -> None:
    game = await _seed_game(session)
    llm = ScriptedLLM([STRIPPABLE_TEXT])
    settings = get_settings().model_copy(update={"commentary_max_retries": 0})

    result = await generate_commentary(
        session, game.id, "club", settings=settings, client=llm
    )

    assert result.attempts == 1
    assert result.validated is True
    assert len(llm.prompts) == 1
    assert "слон на e6" not in result.text
    assert result.text == "Партия началась с хода d4."


async def test_last_attempt_keeps_illegal_move_behaviour(
    session: AsyncSession,
) -> None:
    """Нелегальные ходы по-прежнему дают LLMValidationError, а не обрезку."""
    game = await _seed_game(session)
    llm = ScriptedLLM([ILLEGAL_TEXT])
    settings = get_settings().model_copy(update={"commentary_max_retries": 0})

    with pytest.raises(LLMValidationError, match="Qxf7"):
        await generate_commentary(
            session, game.id, "club", settings=settings, client=llm
        )
    assert len(llm.prompts) == 1


# --- комбинированная подсказка --------------------------------------------


async def test_combined_hints_after_mixed_failures(
    session: AsyncSession,
) -> None:
    """Семантическая проблема + нелегальный ход → в подсказке оба блока."""
    game = await _seed_game(session)
    llm = ScriptedLLM([HALLUCINATED_TEXT, ILLEGAL_TEXT, LEGAL_TEXT])

    result = await generate_commentary(session, game.id, "club", client=llm)

    assert result.attempts == 3
    assert result.text == LEGAL_TEXT
    assert "слон на e6" in llm.prompts[1]
    # Третья попытка: предупреждение и про ходы, и про фигуру.
    assert "Qxf7" in llm.prompts[2]
    assert "слон на e6" in llm.prompts[2]


# --- флаг отключения ------------------------------------------------------


async def test_semantic_validation_can_be_disabled(
    session: AsyncSession,
) -> None:
    game = await _seed_game(session)
    llm = ScriptedLLM([HALLUCINATED_TEXT])
    settings = get_settings().model_copy(
        update={"semantic_validation_enabled": False}
    )

    result = await generate_commentary(
        session, game.id, "club", settings=settings, client=llm
    )

    assert result.attempts == 1
    assert result.text == HALLUCINATED_TEXT
    assert len(llm.prompts) == 1


async def test_mixed_text_not_retried_when_flag_off(
    session: AsyncSession,
) -> None:
    """При выключенном флаге работает только SAN-валидация (как раньше)."""
    game = await _seed_game(session)
    llm = ScriptedLLM([MIXED_TEXT, LEGAL_TEXT])
    settings = get_settings().model_copy(
        update={"semantic_validation_enabled": False}
    )

    result = await generate_commentary(
        session, game.id, "club", settings=settings, client=llm
    )

    # MIXED_TEXT содержит нелегальный Qxf7 → обычный retry по ходам.
    assert result.attempts == 2
    assert result.text == LEGAL_TEXT
    assert "Qxf7" in llm.prompts[1]
    assert "слон на e6" not in llm.prompts[1]
