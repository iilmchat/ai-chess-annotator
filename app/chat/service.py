"""Чат по партии (T-022).

Диалог отличается от генерации комментария (T-016) тем, что текст запроса
приходит от пользователя, а не из пакета фактов. Всё остальное то же:
контекст собирается только из проверенного анализа, ответ проходит через
валидатор SAN-ходов, при нелегальных ходах — retry.

Почему отдельный сервис: сценарий (загрузка партии → контекст → LLM →
валидация → retry) тестируется без HTTP и без реальной модели.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.commentary.service import (
    load_analyses,
    load_game,
    pv_positions,
)
from app.config import Settings, get_settings
from app.engine.models import MoveAnalysis
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import LLMValidationError, NotFoundError
from app.ingestion import ParsedGame, parse_pgn
from app.llm import BaseLLMClient, LLMUsage, get_llm_client
from app.llm.prompts import build_fact_package

__all__ = [
    "CHAT_SYSTEM_PROMPT",
    "ChatAnswer",
    "answer_question",
    "build_chat_prompt",
]

#: Системная роль чата (SPEC §2.7): отвечать только по фактам анализа.
CHAT_SYSTEM_PROMPT = (
    "Ты — шахматный ассистент. Отвечай на вопрос пользователя, опираясь "
    "только на факты из предоставленного анализа партии. Если в анализе "
    "нет ответа, честно скажи об этом. Не придумывай ходы, оценки и "
    "мотивы, которых нет в пакете фактов. Ходы пиши в SAN без Markdown."
)


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    """Ответ чата: текст, расход токенов и число обращений к LLM."""

    reply: str
    usage: LLMUsage
    attempts: int


async def answer_question(
    session: AsyncSession,
    analysis_id: int,
    level: str,
    message: str,
    *,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
) -> ChatAnswer:
    """Ответить на вопрос пользователя по сохранённому анализу партии.

    Args:
        session: сессия БД.
        analysis_id: `games.id`, для которого уже выполнен `/v1/analyze`.
        level: ``beginner`` / ``club`` / ``gm`` — выбор LLM и рекомендаций.
        message: вопрос пользователя.
        settings: настройки (по умолчанию — из окружения).
        client: готовый LLM-клиент (тесты); если задан, не закрывается.

    Raises:
        NotFoundError: партия не найдена или у неё нет анализа ходов.
        LLMValidationError: нелегальные ходы даже после retry.
    """
    cfg = settings or get_settings()
    question = message.strip()
    if not question:
        raise ValueError("message не может быть пустым")

    game = await load_game(session, analysis_id)
    parsed = parse_pgn(game.pgn)
    analyses = await load_analyses(session, game, parsed)
    if not analyses:
        raise NotFoundError(
            f"Для партии {analysis_id} нет анализа ходов — "
            "сначала вызовите /v1/analyze"
        )

    package = build_fact_package(parsed, analyses, level)
    prompt = build_chat_prompt(package, question)
    llm = client if client is not None else get_llm_client(level, cfg)
    owns_client = client is None
    try:
        reply, usage, attempts = await _answer_with_validation(
            llm,
            prompt,
            parsed,
            analyses,
            cfg.chat_max_retries,
        )
    finally:
        if owns_client:
            await llm.aclose()

    return ChatAnswer(reply=reply, usage=usage, attempts=attempts)


def build_chat_prompt(fact_package: str, question: str) -> str:
    """Собрать пользовательский промпт: факты анализа + вопрос."""
    return (
        f"{fact_package}\n\n"
        "# Вопрос пользователя\n"
        f"{question}\n\n"
        "Ответь кратко и по делу, опираясь только на факты выше. "
        "Если вопрос про конкретный ход, назови его в SAN и объясни причину "
        "оценки из пакета."
    )


async def _answer_with_validation(
    llm: BaseLLMClient,
    prompt: str,
    game: ParsedGame,
    analyses: list[MoveAnalysis],
    max_retries: int,
) -> tuple[str, LLMUsage, int]:
    """Сгенерировать ответ; при нелегальных ходах — переспросить."""
    mainline = [move.san for move in game.moves]
    extra_fens = pv_positions(game, analyses)
    illegal: list[str] = []
    for attempt in range(1, max_retries + 2):
        current = prompt if not illegal else _retry_prompt(prompt, illegal)
        result = await llm.complete(current, system_prompt=CHAT_SYSTEM_PROMPT)
        moves = extract_moves_from_text(result.text)
        legal, illegal = validate_moves(
            moves,
            game.initial_fen,
            mainline_san=mainline,
            extra_fens=extra_fens,
        )
        if legal:
            return result.text, result.usage, attempt

    raise LLMValidationError(
        "LLM вернула нелегальные ходы после "
        f"{max_retries + 1} попыток: {', '.join(illegal)}"
    )


def _retry_prompt(prompt: str, illegal: list[str]) -> str:
    bad = ", ".join(illegal)
    return (
        f"{prompt}\n\n"
        "ВНИМАНИЕ: в предыдущем ответе были ходы, которых нет ни в партии, "
        f"ни в вариантах движка: {bad}. Ответь заново, используя ТОЛЬКО ходы "
        "из пакета фактов. Не упоминай ходы, которых там нет."
    )
