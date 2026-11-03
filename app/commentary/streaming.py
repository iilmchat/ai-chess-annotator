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
:class:`CjkStreamBuffer` — буфер копит чанки до конца предложения или
~20–30 символов и вырезает предложения с китайскими иероглифами. В
`delta` иероглифы не попадают; если после очистки текста не осталось,
клиент получает `event: error` (`llm_empty`) без записи комментария.
"""

from __future__ import annotations

import re
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
from app.commentary.semantic import (
    count_cjk,
    has_cjk,
    sanitize_cjk,
    strip_cjk_sentences,
)
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
from app.streaming import chunk_text, sse_event

__all__ = ["CjkStreamBuffer", "stream_commentary"]

logger = structlog.get_logger(__name__)

#: Конец предложения для буферизации SSE-текста (KI-060, итерация 53).
#: Латинские терминаторы требуют пробела/конца (чтобы не резать «1.20» и
#: сокращения), CJK-терминаторы (``。！？``) — безусловная граница: в
#: китайской типографике после них пробела может не быть. Закрывающие
#: кавычки/скобки входят в предложение. Тире и двоеточия границей не
#: считаются.
_SENTENCE_END = re.compile(
    r"(?:[.!?…]+[\"'»)\]]*(?=\s|$)|[。！？]+[\"'»)\]]*)"
)

#: Минимальный буфер, после которого можно резать по границе слова.
MIN_DELTA_CHARS = 20
#: Максимальный буфер: текст без пунктуации отдаём частями по ~30 символов.
MAX_DELTA_CHARS = 30


class CjkStreamBuffer:
    """Буфер SSE-текста, не пропускающий иероглифы в `delta` (KI-060).

    Текст копится и отдаётся клиенту только безопасными кусками: до конца
    предложения либо до ~30 символов по границе слова. Если в куске есть
    CJK-иероглифы, предложения с ними вырезаются
    (:func:`app.commentary.semantic.strip_cjk_sentences`); если после
    вырезания не осталось текста, кусок отбрасывается целиком. Иероглифы
    **никогда** не попадают в возвращаемый текст — только в счётчики.

    Args:
        min_chars: минимальный размер куска при резке по пробелу.
        max_chars: максимальный размер буфера до принудительной резки.

    Raises:
        ValueError: параметры вне допустимого диапазона.
    """

    def __init__(
        self,
        *,
        min_chars: int = MIN_DELTA_CHARS,
        max_chars: int = MAX_DELTA_CHARS,
    ) -> None:
        if min_chars < 1:
            raise ValueError("min_chars должен быть ≥ 1")
        if max_chars < min_chars:
            raise ValueError("max_chars должен быть ≥ min_chars")
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._buffer = ""
        self._stripped_cjk_chars = 0
        self._stripped_units = 0

    @property
    def pending(self) -> str:
        """Текст, ещё не отданный клиенту (для диагностики и тестов)."""
        return self._buffer

    @property
    def stripped_cjk_chars(self) -> int:
        """Сколько иероглифов вырезано/отброшено за весь стрим."""
        return self._stripped_cjk_chars

    @property
    def stripped_units(self) -> int:
        """Сколько кусков пришлось чистить от CJK."""
        return self._stripped_units

    def feed(self, chunk: str) -> list[str]:
        """Добавить чанк LLM и вернуть безопасные куски для `delta`."""
        if not chunk:
            return []
        self._buffer += chunk
        pieces: list[str] = []
        while True:
            unit = self._take_unit()
            if unit is None:
                break
            pieces.extend(self._clean_unit(unit))
        return pieces

    def flush(self) -> list[str]:
        """Отдать остаток буфера в конце стрима."""
        unit, self._buffer = self._buffer, ""
        return self._clean_unit(unit)

    def _take_unit(self) -> str | None:
        """Вырезать из буфера следующий кусок, если он уже «созрел»."""
        if not self._buffer:
            return None
        match = _SENTENCE_END.search(self._buffer)
        if match is not None:
            end = match.end()
            # Пробелы после терминатора — часть куска: так очистка
            # предложения с CJK не склеивает соседние слова.
            while end < len(self._buffer) and self._buffer[end].isspace():
                end += 1
            unit = self._buffer[:end]
            self._buffer = self._buffer[end:]
            return unit
        if len(self._buffer) < self._min_chars:
            return None
        limit = min(len(self._buffer), self._max_chars)
        cut = self._buffer.rfind(" ", 0, limit + 1)
        if cut >= self._min_chars - 1:
            unit = self._buffer[: cut + 1]
            self._buffer = self._buffer[cut + 1 :]
            return unit
        if len(self._buffer) >= self._max_chars:
            unit = self._buffer[: self._max_chars]
            self._buffer = self._buffer[self._max_chars :]
            return unit
        return None

    def _clean_unit(self, unit: str) -> list[str]:
        """Очистить кусок от CJK; вернуть `[]`, если текста не осталось."""
        if not unit:
            return []
        if not has_cjk(unit):
            return [unit]
        stripped, _removed = strip_cjk_sentences(unit)
        self._stripped_cjk_chars += count_cjk(unit)
        self._stripped_units += 1
        if not stripped.strip():
            return []
        # Сохраняем границы слов, если вырезание «съело» пробел.
        if unit[:1].isspace() and not stripped[:1].isspace():
            stripped = " " + stripped
        if unit[-1:].isspace() and not stripped[-1:].isspace():
            stripped += " "
        return [stripped]



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
