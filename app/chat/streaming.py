"""SSE-ответ чата по партии (T-032).

События те же, что у комментария: `meta`, `delta`, `done`, `error`.
Итоговый текст проверяется SAN-валидатором; при нелегальных ходах —
`event: error` без сохранения.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import CHAT_SYSTEM_PROMPT, build_chat_prompt
from app.commentary.service import load_analyses, load_game, pv_positions
from app.config import Settings, get_settings
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import AppError
from app.ingestion import parse_pgn
from app.llm import BaseLLMClient, get_llm_client
from app.llm.prompts import build_fact_package
from app.llm.tokens import estimate_tokens
from app.streaming import sse_event

__all__ = ["stream_chat"]

logger = structlog.get_logger(__name__)


async def stream_chat(
    session: AsyncSession,
    analysis_id: int,
    level: str,
    message: str,
    *,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Ответить на вопрос потоком SSE-событий."""
    cfg = settings or get_settings()
    llm: BaseLLMClient | None = None
    owns_client = client is None
    try:
        game = await load_game(session, analysis_id)
        parsed = parse_pgn(game.pgn)
        analyses = await load_analyses(session, game, parsed)
        if not analyses:
            yield sse_event(
                "error",
                code="not_found",
                detail=(
                    f"Для партии {analysis_id} нет анализа — сначала вызовите "
                    "/v1/analyze"
                ),
            )
            return

        package = build_fact_package(parsed, analyses, level)
        prompt = build_chat_prompt(package, message.strip())
        llm = client if client is not None else get_llm_client(level, cfg)
        yield sse_event("meta", model=llm.model, level=level)

        chunks: list[str] = []
        async for chunk in llm.stream_generate(
            prompt, system_prompt=CHAT_SYSTEM_PROMPT
        ):
            chunks.append(chunk)
            yield sse_event("delta", text=chunk)

        reply = "".join(chunks).strip()
        if not reply:
            yield sse_event(
                "error", code="llm_empty", detail="Модель вернула пустой ответ"
            )
            return

        moves = extract_moves_from_text(reply)
        legal, illegal = validate_moves(
            moves,
            parsed.initial_fen,
            mainline_san=[move.san for move in parsed.moves],
            extra_fens=pv_positions(parsed, analyses),
        )
        if not legal:
            yield sse_event(
                "error",
                code="llm_invalid_output",
                detail="Модель упомянула нелегальные ходы",
                illegal=illegal,
            )
            return

        tokens_in = estimate_tokens(prompt) + estimate_tokens(CHAT_SYSTEM_PROMPT)
        tokens_out = estimate_tokens(reply)
        yield sse_event(
            "done",
            tokens_used=tokens_in + tokens_out,
            model=llm.model,
            validated=True,
        )
    except AppError as exc:
        yield sse_event("error", code=exc.code, detail=exc.message)
    except Exception as exc:
        logger.exception("stream_chat_failed", error=str(exc))
        yield sse_event(
            "error", code="internal_error", detail="Внутренняя ошибка стриминга"
        )
    finally:
        if llm is not None and owns_client:
            await llm.aclose()
