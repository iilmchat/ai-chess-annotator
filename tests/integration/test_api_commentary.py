"""Интеграционные тесты `POST /v1/commentary` (T-016).

Две группы:

* быстрые тесты на БД с подставной LLM (ретраи, валидация, реконструкция
  анализа из `moves`/`motifs`/`multipv`);
* живой тест через API с LM Studio (скипается, если провайдера нет).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.commentary import generate_commentary, load_analyses
from app.config import get_settings
from app.db.models import Commentary, Game, Move
from app.errors import LLMValidationError, NotFoundError
from app.ingestion import parse_pgn
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.main import create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")
SHORT_PGN = "1. d4 Nf6 2. c4 e6 1/2-1/2"

ILLEGAL_TEXT = "Белые сыграли Qxf7 и выиграли партию."
LEGAL_TEXT = "Партия началась с хода d4, затем последовало Nf6."
ILLEGAL_TEXT_EN = "White played Qxf7 and won the game."
LEGAL_TEXT_EN = "The game began with d4, followed by Nf6."


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
        text = self._texts.pop(0) if len(self._texts) > 1 else self._texts[0]
        return LLMResult(
            text=text,
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


def _lmstudio_available() -> bool:
    import httpx

    settings = get_settings()
    try:
        response = httpx.get(
            f"{settings.vllm_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.vllm_api_key}"},
            timeout=3.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _engine_available() -> bool:
    settings = get_settings()
    return Path(settings.stockfish_path).is_file() or (
        shutil.which(settings.stockfish_path) is not None
    )


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


async def _seed_game_db(pgn: str = SHORT_PGN) -> int:
    """Создать партию+ходы в отдельной сессии и вернуть id (для sync-тестов)."""
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            game = await _seed_game(db_session, pgn)
            await db_session.commit()
            return game.id
    finally:
        await engine.dispose()


# --- реконструкция анализа из БД -----------------------------------------


async def test_load_analyses_reconstructs_moves(session: AsyncSession) -> None:
    game = await _seed_game(session)
    parsed = parse_pgn(game.pgn)

    analyses = await load_analyses(session, game, parsed)

    assert [item.ply for item in analyses] == [1, 2, 3, 4]
    assert analyses[0].played_move_san == "d4"
    assert analyses[0].played_move_uci == "d2d4"
    assert analyses[0].eval_after == 20  # белые: +20 cp
    assert analyses[1].eval_after == -20  # чёрные: их оценка противоположна
    assert analyses[0].lines == []
    assert analyses[0].motifs == []


# --- ретраи и валидация ---------------------------------------------------


async def test_retry_after_illegal_moves(session: AsyncSession) -> None:
    game = await _seed_game(session)
    llm = ScriptedLLM([ILLEGAL_TEXT, LEGAL_TEXT])

    result = await generate_commentary(
        session, game.id, "club", client=llm
    )

    assert result.attempts == 2
    assert result.text == LEGAL_TEXT
    assert result.validated is True
    assert len(llm.prompts) == 2
    assert "ВНИМАНИЕ" in llm.prompts[1]
    assert "Qxf7" in llm.prompts[1]


async def test_raises_after_retries_exhausted(session: AsyncSession) -> None:
    game = await _seed_game(session)
    llm = ScriptedLLM([ILLEGAL_TEXT])
    settings = get_settings().model_copy(update={"commentary_max_retries": 0})

    with pytest.raises(LLMValidationError, match="Qxf7"):
        await generate_commentary(
            session, game.id, "club", settings=settings, client=llm
        )
    assert len(llm.prompts) == 1


async def test_english_language_uses_english_prompt_and_retry(
    session: AsyncSession,
) -> None:
    """T-106: language=en выбирает *_en.txt и англоязычный retry."""
    game = await _seed_game(session)
    llm = ScriptedLLM([ILLEGAL_TEXT_EN, LEGAL_TEXT_EN])

    result = await generate_commentary(
        session, game.id, "club", language="en", client=llm
    )

    assert result.text == LEGAL_TEXT_EN
    assert result.attempts == 2
    assert llm.system_prompts[0] is not None
    assert "Write in English" in llm.system_prompts[0]
    assert "WARNING" in llm.prompts[1]
    assert "Qxf7" in llm.prompts[1]


async def test_unknown_game_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await generate_commentary(session, 999_999_999, "club", client=ScriptedLLM([LEGAL_TEXT]))


# --- HTTP-контракт (быстрые) ---------------------------------------------


def _client() -> TestClient:
    settings = get_settings().model_copy(
        update={
            "rate_limit_requests": 1000,
            "llm_warmup_enabled": False,
            "commentary_max_retries": 2,
        }
    )
    return TestClient(create_app(settings))


def test_commentary_unknown_analysis_returns_404() -> None:
    with _client() as client:
        response = client.post(
            "/v1/commentary", json={"analysis_id": 999_999_999, "level": "club"}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_commentary_validates_level_and_id() -> None:
    with _client() as client:
        bad_level = client.post(
            "/v1/commentary", json={"analysis_id": 1, "level": "master"}
        )
        bad_id = client.post(
            "/v1/commentary", json={"analysis_id": 0, "level": "club"}
        )

    assert bad_level.status_code == 422
    assert bad_id.status_code == 422


def test_commentary_language_en_via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-106: `language=en` и Accept-Language дают английский prompt."""
    game_id = asyncio.run(_seed_game_db())
    llm = ScriptedLLM([LEGAL_TEXT_EN])
    monkeypatch.setattr(
        "app.commentary.service.get_llm_client",
        lambda level, cfg: llm,
    )
    try:
        with _client() as client:
            explicit = client.post(
                "/v1/commentary",
                json={
                    "analysis_id": game_id,
                    "level": "club",
                    "language": "en",
                },
            )
            header_only = client.post(
                "/v1/commentary",
                json={"analysis_id": game_id, "level": "club"},
                headers={"Accept-Language": "en-US,en;q=0.9"},
            )
    finally:
        asyncio.run(_delete_game(game_id))

    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["text"] == LEGAL_TEXT_EN
    assert header_only.status_code == 200, header_only.text
    assert header_only.json()["text"] == LEGAL_TEXT_EN
    assert len(llm.system_prompts) == 2
    for system_prompt in llm.system_prompts:
        assert system_prompt is not None
        assert "Write in English" in system_prompt


# --- живой тест через API -------------------------------------------------


@pytest.mark.skipif(not _lmstudio_available(), reason="LM Studio недоступен")
@pytest.mark.skipif(not _engine_available(), reason="Stockfish не найден")
def test_live_club_commentary_persisted_and_validated() -> None:
    with _client() as client:
        analyze = client.post(
            "/v1/analyze", json={"pgn": EXAMPLE_PGN, "level": "club"}
        )
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])

        started = time.perf_counter()
        try:
            response = client.post(
                "/v1/commentary",
                json={"analysis_id": analysis_id, "level": "club"},
            )
            elapsed_ms = (time.perf_counter() - started) * 1000

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["validated"] is True
            assert len(body["text"]) > 100
            assert body["tokens_used"] > 0
            assert body["commentary_id"] > 0
            assert elapsed_ms < 180_000

            stored = asyncio.run(_read_commentaries(analysis_id))
            assert stored, "комментарий не сохранён в БД"
            assert stored[-1].text == body["text"]
            assert stored[-1].validated is True
            assert stored[-1].level == "club"
        finally:
            asyncio.run(_delete_game(analysis_id))


async def _read_commentaries(game_id: int) -> list[Commentary]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            rows = (
                await db_session.scalars(
                    select(Commentary)
                    .where(Commentary.game_id == game_id)
                    .order_by(Commentary.id)
                )
            ).all()
    finally:
        await engine.dispose()
    return list(rows)


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                Game.__table__.delete().where(Game.id == game_id)
            )
    finally:
        await engine.dispose()
