"""SSE-ответ чата по партии (T-032).

События те же, что у комментария: `meta`, `delta`, `done`, `error`.
Итоговый текст проверяется SAN-валидатором; при нелегальных ходах —
`event: error` без сохранения.

KI-060 (итерация 57): текст, уходящий в `delta`, проходит через
:class:`app.streaming.CjkStreamBuffer` — так же, как в комментарии. Legacy
cache-запись с иероглифами очищается :func:`app.commentary.semantic.sanitize_cjk`;
если чистить нечего — генерируем ответ заново. В лог — только счётчики.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import (
    chat_position_fen,
    lookup,
    make_prompt_hash,
    model_for_level,
    store,
)
from app.chat.service import (
    build_chat_prompt,
    format_chat_history,
    format_selected_ply_context,
    load_chat_system_prompt,
)
from app.commentary.semantic import sanitize_cjk
from app.commentary.service import load_analyses, load_game, pv_positions
from app.config import Settings, get_settings
from app.engine.validator import extract_moves_from_text, validate_moves
from app.errors import AppError
from app.i18n import localize_error_detail
from app.ingestion import parse_pgn
from app.llm import BaseLLMClient, get_llm_client
from app.llm.calls import record_cache_hit
from app.llm.prompts import build_fact_package
from app.llm.tokens import estimate_tokens
from app.prompts import resolve_language
from app.security.guardrails import sanitize_user_input
from app.streaming import CjkStreamBuffer, chunk_text, sse_event

__all__ = ["stream_chat"]

logger = structlog.get_logger(__name__)


def _meta_event(
    *,
    model: str,
    level: str,
    language: str,
    selected_ply: int | None,
) -> dict[str, str]:
    """`meta`-событие чата; `selected_ply` — только если он задан (T-072)."""
    payload: dict[str, object] = {
        "model": model,
        "level": level,
        "language": language,
    }
    if selected_ply is not None:
        payload["selected_ply"] = selected_ply
    return sse_event("meta", **payload)


async def stream_chat(
    session: AsyncSession,
    analysis_id: int,
    level: str,
    message: str,
    *,
    history: list[dict[str, str]] | None = None,
    settings: Settings | None = None,
    client: BaseLLMClient | None = None,
    language: str = "ru",
    selected_ply: int | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Ответить на вопрос потоком SSE-событий.

    Args:
        language: ``ru``/``en`` — язык ответа и системного промпта
            (T-106/KI-058).
        selected_ply: полуход, который пользователь смотрит на доске
            (T-072); ``None``/вне анализа — без строки контекста.
    """
    cfg = settings or get_settings()
    lang = resolve_language(language, None, cfg.default_language)
    system_prompt = load_chat_system_prompt(lang)
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
                detail=localize_error_detail(
                    "not_found",
                    f"Для партии {analysis_id} нет анализа — сначала вызовите "
                    "/v1/analyze",
                    lang,
                ),
            )
            return

        question = sanitize_user_input(message)
        history_text = format_chat_history(history)
        model = client.model if client is not None else model_for_level(cfg, level)
        # Строка выбранного полухода входит в user-промпт и в prompt_hash.
        context_line = format_selected_ply_context(
            selected_ply, analyses, language=lang
        )
        hashed_question = (
            f"{context_line}\n{question}" if context_line else question
        )
        prompt_hash_value = make_prompt_hash(
            level,
            model,
            system_prompt,
            question=hashed_question,
            history=history_text,
        )
        position_fen = chat_position_fen(parsed)
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
            # KI-060 (итерация 57): старые cache-записи могли сохраниться до
            # CJK-фильтра. Клиенту иероглифы не отдаём: чистим текст; если
            # чистить нечего — считаем кэш непригодным и генерируем заново.
            cached = sanitize_cjk(hit.text)
            if not cached.rejected and cached.text.strip():
                # KI-046: cache-hit в чате — тоже строка llm_calls
                # (operation=chat).
                record_cache_hit(latency_ms=lookup_ms)
                yield _meta_event(
                    model="semantic-cache",
                    level=level,
                    language=lang,
                    selected_ply=selected_ply,
                )
                for chunk in chunk_text(cached.text):
                    yield sse_event("delta", text=chunk)
                yield sse_event(
                    "done",
                    tokens_used=0,
                    model="semantic-cache",
                    validated=True,
                )
                return
            logger.warning(
                "chat_cache_cjk_dropped",
                cjk_chars=cached.cjk_chars,
                cjk_share=round(cached.share, 3),
                removed_sentences=cached.removed_sentences,
            )

        package = build_fact_package(parsed, analyses, level)
        prompt = build_chat_prompt(
            package,
            question,
            history,
            language=lang,
            selected_ply=selected_ply,
            analyses=analyses,
        )
        llm = client if client is not None else get_llm_client(level, cfg)
        llm.set_level(level)
        yield _meta_event(
            model=llm.model,
            level=level,
            language=lang,
            selected_ply=selected_ply,
        )

        cjk_filter = CjkStreamBuffer()
        emitted: list[str] = []
        async for chunk in llm.stream_generate(
            prompt, system_prompt=system_prompt
        ):
            for safe_chunk in cjk_filter.feed(chunk):
                emitted.append(safe_chunk)
                yield sse_event("delta", text=safe_chunk)
        for safe_chunk in cjk_filter.flush():
            emitted.append(safe_chunk)
            yield sse_event("delta", text=safe_chunk)
        if cjk_filter.stripped_cjk_chars:
            # В лог — только счётчики, без текста LLM (KI-060).
            logger.warning(
                "chat_cjk_stream_stripped",
                cjk_chars=cjk_filter.stripped_cjk_chars,
                stripped_units=cjk_filter.stripped_units,
            )

        reply = "".join(emitted).strip()
        if not reply:
            detail = (
                "Модель вернула только китайские иероглифы — ответ "
                "отфильтрован"
                if cjk_filter.stripped_cjk_chars
                else localize_error_detail(
                    "llm_empty", "Модель вернула пустой ответ", lang
                )
            )
            yield sse_event("error", code="llm_empty", detail=detail)
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
                detail=localize_error_detail(
                    "llm_invalid_output",
                    "Модель упомянула нелегальные ходы",
                    lang,
                ),
                illegal=illegal,
            )
            return

        tokens_in = estimate_tokens(prompt) + estimate_tokens(system_prompt)
        tokens_out = estimate_tokens(reply)
        stored = await store(
            session,
            fen=position_fen,
            level=level,
            prompt_hash_value=prompt_hash_value,
            response_text=reply,
            settings=cfg,
        )
        if stored:
            await session.commit()
        yield sse_event(
            "done",
            tokens_used=tokens_in + tokens_out,
            model=llm.model,
            validated=True,
        )
    except AppError as exc:
        yield sse_event(
            "error",
            code=exc.code,
            detail=localize_error_detail(exc.code, exc.message, lang),
        )
    except Exception as exc:
        logger.exception("stream_chat_failed", error=str(exc))
        yield sse_event(
            "error", code="internal_error", detail="Внутренняя ошибка стриминга"
        )
    finally:
        if llm is not None and owns_client:
            await llm.aclose()
