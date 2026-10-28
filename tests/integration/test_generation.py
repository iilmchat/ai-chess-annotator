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

from app.commentary.service import pv_positions
from app.config import get_settings
from app.engine.pool import EnginePool
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import LLMTimeoutError
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

    settings = get_settings().model_copy(
        update={"llm_temperature": 0.0, "llm_timeout_s": 300.0}
    )
    llm = get_llm_client(LEVEL, settings)
    try:
        text, illegal = await _generate_until_legal(llm, package, game, analyses)
    except LLMTimeoutError as exc:
        pytest.skip(f"LM Studio не уложилась в таймаут (KI-024): {exc}")
    finally:
        await llm.aclose()

    assert len(text) > 200, "модель вернула слишком короткий текст"
    assert illegal == [], f"LLM придумала нелегальные ходы: {illegal}"


async def _generate_until_legal(
    llm: object,
    package: str,
    game: object,
    analyses: list[object],
    *,
    attempts: int = 2,
) -> tuple[str, list[str]]:
    """Сгенерировать текст, повторив запрос при нелегальных ходах.

    Повторяет поведение `POST /v1/commentary` (там тоже до 2 попыток):
    проверяем не «идеальность модели с первого раза», а способность
    пайплайна (промпт + валидатор + retry) выдать легальный текст.
    """
    prompt = package
    text = ""
    illegal: list[str] = []
    for _ in range(attempts):
        result = await llm.complete(prompt, system_prompt=load_system_prompt(LEVEL))  # type: ignore[attr-defined]
        text = result.text
        moves = extract_moves_from_text(text)
        legal, illegal = validate_moves(
            moves,
            game.initial_fen,  # type: ignore[attr-defined]
            mainline_san=[move.san for move in game.moves],  # type: ignore[attr-defined]
            extra_fens=pv_positions(game, analyses),  # type: ignore[arg-type]
        )
        if legal:
            return text, []
        prompt = (
            f"{package}\n\nВНИМАНИЕ: в предыдущем ответе были ходы, которых нет "
            f"в пакете фактов: {', '.join(illegal)}. Перепиши, используя только "
            "ходы из пакета."
        )
    return text, illegal
