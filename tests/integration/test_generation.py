"""E2E: пакет фактов → LM Studio → валидатор (T-014/T-015).

Тест идёт в живую локальную модель и требует Stockfish. Если LM Studio
недоступна (CI, другая машина), модуль скипается — `pytest -q` остаётся
зелёным без сети.

Проверяется главное обещание SPEC §2.6: **ни один ход из ответа LLM не
проходит без проверки на доске**.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.engine.pool import EnginePool
from app.engine.validator import extract_moves_from_text, validate_moves
from app.ingestion import parse_pgn
from app.llm import get_llm_client
from app.llm.prompts import build_fact_package
from app.prompts import load_system_prompt

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")

LEVEL = "club"


async def _lmstudio_responds() -> bool:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}
    url = f"{settings.vllm_base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _lmstudio_available() -> bool:
    try:
        return asyncio.run(_lmstudio_responds())
    except Exception:
        return False


def _engine_available() -> bool:
    settings = get_settings()
    return Path(settings.stockfish_path).is_file() or (
        shutil.which(settings.stockfish_path) is not None
    )


pytestmark = [
    pytest.mark.skipif(
        not _lmstudio_available(), reason="LM Studio недоступен (KI-022)"
    ),
    pytest.mark.skipif(not _engine_available(), reason="Stockfish не найден (KI-006)"),
]


async def test_club_commentary_has_only_legal_moves() -> None:
    game = parse_pgn(EXAMPLE_PGN)

    async with EnginePool(get_settings()) as pool:
        analyses = await pool.analyze_game_moves(
            game.moves, LEVEL, initial_fen=game.initial_fen
        )

    assert len(analyses) == game.ply_count

    package = build_fact_package(game, analyses, LEVEL)
    assert "Ключевые моменты" in package

    llm = get_llm_client(LEVEL)
    try:
        result = await llm.complete(package, system_prompt=load_system_prompt(LEVEL))
    finally:
        await llm.aclose()

    assert len(result.text) > 200, "модель вернула слишком короткий текст"

    text_moves = extract_moves_from_text(result.text)
    assert text_moves, "в комментарии нет ни одного шахматного хода"

    legal, illegal = validate_moves(
        text_moves,
        game.initial_fen,
        mainline_san=[move.san for move in game.moves],
    )

    assert legal, f"LLM придумала нелегальные ходы: {illegal}"
    assert illegal == []
