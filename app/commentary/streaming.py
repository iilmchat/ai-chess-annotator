"""SSE-генерация комментария (T-032).

Поток событий:

1. `meta` — модель и уровень (клиент показывает «печатает…»);
2. `delta` — куски текста;
3. `done` — `commentary_id`, оценка токенов, validated=true;
4. `error` — `{code, detail, illegal?}`.

После стрима итоговый текст проверяется тем же SAN-валидатором, что и
`POST /v1/commentary`. Нелегальные ходы → `event: error`, запись **не**
создаётся. Retry после частично показанного текста не делаем: клиент уже
увидел ответ, честнее показать ошибку.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import (
    commentary_position_fen,
    lookup,
    make_prompt_hash,
    model_for_level,
    store,
)
from app.commentary.service import load_analyses, load_game, pv_positions
from app.config import Settings, get_settings
from app.db.models import Commentary, Level
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import AppError
from app.finops import compute_cost_usd, prices_for_level
from app.ingestion import parse_pgn
from app.llm import BaseLLMClient, get_llm_client
from app.llm.prompts import build_fact_package
from app.llm.tokens import estimate_tokens
from app.prompts import load_system_prompt
from app.streaming import chunk_text, sse_event

__all__ = ["stream_commentary"]

logger = structlog.get_logger(__name__)


async def stream_commentary(
    session: AsyncSession,
    game_id: int,
    level: str,
    style: str | None = None,
    *,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Сгенерировать комментарий потоком SSE-событий."""
    cfg = settings or get_settings()
    llm: BaseLLMClient | None = None
    owns_client = client is None
    try:
        game = await load_game(session, game_id)
        parsed = parse_pgn(game.pgn)
        analyses = await load_analyses(session, game, parsed)
        if not analyses:
            yield sse_event(
                "error",
                code="not_found",
                detail=(
                    f"Для партии {game_id} нет анализа — сначала вызовите "
                    "/v1/analyze"
                ),
            )
            return

        system_prompt = await load_system_prompt(level, session)
        if style:
            system_prompt = f"{system_prompt}\n\nДОПОЛНИТЕЛЬНЫЙ СТИЛЬ: {style}"

        model = client.model if client is not None else model_for_level(cfg, level)
        prompt_hash_value = make_prompt_hash(
            level, model, system_prompt, style=style
        )
        position_fen = commentary_position_fen(parsed, analyses)
        hit = await lookup(
            session,
            fen=position_fen,
            level=level,
            prompt_hash_value=prompt_hash_value,
            settings=cfg,
        )
        if hit is not None:
            row = Commentary(
                game_id=game.id,
                level=Level(level),
                text=hit.text,
                model="semantic-cache",
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
                validated=True,
            )
            session.add(row)
            await session.flush()
            await session.commit()
            yield sse_event("meta", model="semantic-cache", level=level)
            for chunk in chunk_text(hit.text):
                yield sse_event("delta", text=chunk)
            yield sse_event(
                "done",
                commentary_id=row.id,
                tokens_used=0,
                model="semantic-cache",
                validated=True,
            )
            return

        package = build_fact_package(parsed, analyses, level)
        llm = client if client is not None else get_llm_client(level, cfg)
        llm.set_level(level)
        yield sse_event("meta", model=llm.model, level=level)

        chunks: list[str] = []
        async for chunk in llm.stream_generate(package, system_prompt=system_prompt):
            chunks.append(chunk)
            yield sse_event("delta", text=chunk)

        text = "".join(chunks).strip()
        if not text:
            yield sse_event(
                "error", code="llm_empty", detail="Модель вернула пустой ответ"
            )
            return

        moves = extract_moves_from_text(text)
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

        tokens_in = estimate_tokens(package) + estimate_tokens(system_prompt)
        tokens_out = estimate_tokens(text)
        price_in, price_out = prices_for_level(cfg, level)
        row = Commentary(
            game_id=game.id,
            level=Level(level),
            text=text,
            model=llm.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=compute_cost_usd(
                tokens_in,
                tokens_out,
                price_in_per_1m=price_in,
                price_out_per_1m=price_out,
            ),
            validated=True,
        )
        await store(
            session,
            fen=position_fen,
            level=level,
            prompt_hash_value=prompt_hash_value,
            response_text=text,
            settings=cfg,
        )
        session.add(row)
        await session.commit()
        yield sse_event(
            "done",
            commentary_id=row.id,
            tokens_used=tokens_in + tokens_out,
            model=llm.model,
            validated=True,
        )
    except AppError as exc:
        yield sse_event("error", code=exc.code, detail=exc.message)
    except Exception as exc:  # поток не должен «зависать» на неизвестной ошибке
        logger.exception("stream_commentary_failed", error=str(exc))
        yield sse_event(
            "error", code="internal_error", detail="Внутренняя ошибка стриминга"
        )
    finally:
        if llm is not None and owns_client:
            await llm.aclose()
