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

KI-060 (итерация 53): текст, уходящий в `delta`, проходит через
:class:`CjkStreamBuffer` (общий с чатом, :mod:`app.streaming`) — буфер
копит чанки до конца предложения или ~20–30 символов и вырезает
предложения с китайскими иероглифами. В `delta` иероглифы не попадают;
если после очистки текста не осталось, клиент получает `event: error`
(`llm_empty`) без записи комментария.
"""

from __future__ import annotations

import time
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
from app.commentary.semantic import has_cjk, sanitize_cjk
from app.commentary.service import load_analyses, load_game, pv_positions
from app.config import Settings, get_settings
from app.db.models import Commentary, Level
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import AppError
from app.finops import compute_cost_usd, prices_for_level
from app.ingestion import parse_pgn
from app.llm import BaseLLMClient, get_llm_client
from app.llm.calls import attach_commentary_id, record_cache_hit
from app.llm.prompts import build_fact_package
from app.llm.tokens import estimate_tokens
from app.prompts import load_system_prompt, resolve_language
from app.streaming import (
    MAX_DELTA_CHARS,
    MIN_DELTA_CHARS,
    CjkStreamBuffer,
    chunk_text,
    sse_event,
)

__all__ = [
    "MAX_DELTA_CHARS",
    "MIN_DELTA_CHARS",
    "CjkStreamBuffer",
    "has_cjk",
    "stream_commentary",
]

logger = structlog.get_logger(__name__)


async def stream_commentary(
    session: AsyncSession,
    game_id: int,
    level: str,
    style: str | None = None,
    *,
    language: str | None = None,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
    user_id: int | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Сгенерировать комментарий потоком SSE-событий.

    Args:
        language: ``ru``/``en`` (T-106); ``None`` — из `DEFAULT_LANGUAGE`.
        user_id: владелец создаваемой записи `commentaries.user_id`
            (T-028/KI-035). ``None`` — служебная/тестовая генерация без
            пользователя (legacy-поведение).
    """
    cfg = settings or get_settings()
    lang = resolve_language(language, None, cfg.default_language)
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

        system_prompt = await load_system_prompt(
            level, session, language=lang
        )
        if style:
            label = (
                "ADDITIONAL STYLE"
                if lang == "en"
                else "ДОПОЛНИТЕЛЬНЫЙ СТИЛЬ"
            )
            system_prompt = f"{system_prompt}\n\n{label}: {style}"

        model = client.model if client is not None else model_for_level(cfg, level)
        prompt_hash_value = make_prompt_hash(
            level, model, system_prompt, style=style
        )
        position_fen = commentary_position_fen(parsed, analyses)
        lookup_started = time.perf_counter()
        hit = await lookup(
            session,
            fen=position_fen,
            level=level,
            prompt_hash_value=prompt_hash_value,
            settings=cfg,
        )
        lookup_ms = int((time.perf_counter() - lookup_started) * 1000)
        if hit is not None:
            # KI-060 (итерация 53): старые cache-записи могли сохраниться до
            # CJK-фильтра. Клиенту иероглифы не отдаём: чистим текст; если
            # чистить нечего — считаем кэш непригодным и генерируем заново.
            cached = sanitize_cjk(hit.text)
            if not cached.rejected and cached.text.strip():
                # KI-046: cache-hit фиксируется в llm_calls и привязывается к
                # созданному комментарию (attach после commit, когда есть id).
                record_cache_hit(latency_ms=lookup_ms)
                row = Commentary(
                    game_id=game.id,
                    user_id=user_id,
                    level=Level(level),
                    text=cached.text,
                    model="semantic-cache",
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0,
                    validated=True,
                )
                session.add(row)
                await session.flush()
                await session.commit()
                attach_commentary_id(row.id)
                yield sse_event(
                    "meta", model="semantic-cache", level=level, language=lang
                )
                for chunk in chunk_text(cached.text):
                    yield sse_event("delta", text=chunk)
                yield sse_event(
                    "done",
                    commentary_id=row.id,
                    tokens_used=0,
                    model="semantic-cache",
                    validated=True,
                )
                return
            logger.warning(
                "commentary_cache_cjk_dropped",
                cjk_chars=cached.cjk_chars,
                cjk_share=round(cached.share, 3),
                removed_sentences=cached.removed_sentences,
            )

        package = build_fact_package(parsed, analyses, level)
        llm = client if client is not None else get_llm_client(level, cfg)
        llm.set_level(level)
        yield sse_event("meta", model=llm.model, level=level, language=lang)

        cjk_filter = CjkStreamBuffer()
        emitted: list[str] = []
        async for chunk in llm.stream_generate(package, system_prompt=system_prompt):
            for safe_chunk in cjk_filter.feed(chunk):
                emitted.append(safe_chunk)
                yield sse_event("delta", text=safe_chunk)
        for safe_chunk in cjk_filter.flush():
            emitted.append(safe_chunk)
            yield sse_event("delta", text=safe_chunk)
        if cjk_filter.stripped_cjk_chars:
            # В лог — только счётчики, без текста LLM (KI-060).
            logger.warning(
                "commentary_cjk_stream_stripped",
                cjk_chars=cjk_filter.stripped_cjk_chars,
                stripped_units=cjk_filter.stripped_units,
            )

        text = "".join(emitted).strip()
        if not text:
            detail = (
                "Модель вернула только китайские иероглифы — ответ отфильтрован"
                if cjk_filter.stripped_cjk_chars
                else "Модель вернула пустой ответ"
            )
            yield sse_event("error", code="llm_empty", detail=detail)
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
            user_id=user_id,
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
        # KI-046: id появился — привязываем уже записанные вызовы LLM
        # (ошибки attach не влияют на стрим, задача фоновая).
        attach_commentary_id(row.id)
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
