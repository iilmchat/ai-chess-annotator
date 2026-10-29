"""Durable-запись вызовов LLM в `llm_calls` (T-030, DESIGN §4.4).

Зачем отдельный модуль:

* **Не блокирует ответ.** Запись — fire-and-forget: `asyncio.create_task` +
  собственная сессия БД; ошибка записи только логируется (`warning`) и
  никогда не роняет запрос (LLMOps не должен влиять на API).
* **Контекст вызова** (операция, пользователь, партия, промпт) передаётся
  через contextvars — его выставляют эндпоинты/сервисы, а клиент LLM о нём
  ничего не знает. Это позволяет записывать вызовы и sync-, и stream-пути
  без протаскивания аргументов через все слои.
* **Только метрики.** Тексты промптов/ответов и любые PII в таблицу не
  попадают: операция, провайдер, модель, токены, стоимость, латентность,
  статус.

Вне HTTP-запроса и вне `llm_call_context` записей нет: юнит-тесты клиента
и служебные скрипты не должны ходить в БД.

KI-046 — полный охват LLMOps:

* **warmup** — прогрев модели тоже пишется (`operation="warmup"`), чтобы
  стартовые вызовы не выпадали из учёта;
* **attach_commentary_id** — после INSERT комментария `llm_calls` этого
  trace получает `commentary_id` (иначе связь «метрика → комментарий»
  терялась: id появляется после вызова LLM);
* **record_cache_hit** — попадание в semantic cache фиксируется как
  отдельный вызов (`provider="cache"`, `cache_hit=True`, 0 токенов/стоимости).
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Level, LlmCall, Prompt
from app.db.session import SessionLocal
from app.tracing import current_request_id, current_trace_id, new_trace_context

__all__ = [
    "CallStatus",
    "LlmCallContext",
    "attach_commentary_id",
    "current_call_context",
    "llm_call_context",
    "record_cache_hit",
    "record_llm_call",
    "stream_in_call_context",
    "wait_for_pending_llm_calls",
]

logger = structlog.get_logger(__name__)

#: Допустимые статусы вызова (DESIGN §4.4: ok | error | timeout | invalid).
CallStatus = Literal["ok", "error", "timeout", "invalid"]

#: Уровни ассистента (совпадает с enum `level`); prompt_slug для них = level.
_LEVEL_VALUES = frozenset({"beginner", "club", "gm"})

#: Запись по умолчанию для вызова без явного контекста (HTTP/фон).
_DEFAULT_OPERATION = "llm"

_current_call: ContextVar[LlmCallContext | None] = ContextVar(
    "llm_call_context", default=None
)

#: Ссылки на фоновые задачи: без них GC может отменить запись.
_pending: set[asyncio.Task[None]] = set()

#: Последний trace/span, записанный recorder-ом в текущем контексте (KI-046).
#: Нужны `attach_commentary_id`, чтобы найти строки `llm_calls` этого запроса
#: (trace_id из HTTP-middleware, а span_id — на случай отсутствия HTTP).
_last_trace_id: ContextVar[UUID | None] = ContextVar(
    "llm_call_last_trace_id", default=None
)
_last_span_id: ContextVar[str | None] = ContextVar(
    "llm_call_last_span_id", default=None
)


@dataclass(frozen=True, slots=True)
class LlmCallContext:
    """Что известно о вызове LLM вне самого клиента (передаётся contextvar-ом)."""

    operation: str
    actor_id: int | None = None
    game_id: int | None = None
    commentary_id: int | None = None
    prompt_slug: str | None = None
    prompt_version: int | None = None
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class _LlmCallRecord:
    """Снимок метрик одного вызова для фоновой записи."""

    trace_id: UUID
    span_id: str
    request_id: str | None
    context: LlmCallContext | None
    provider: str
    model: str
    level: str | None
    status: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    cache_hit: bool
    error_code: str | None


def current_call_context() -> LlmCallContext | None:
    """Текущий контекст вызова LLM (``None`` вне записи)."""
    return _current_call.get()


@contextmanager
def llm_call_context(
    *,
    operation: str,
    actor_id: int | None = None,
    game_id: int | None = None,
    commentary_id: int | None = None,
    prompt_slug: str | None = None,
    prompt_version: int | None = None,
    cache_hit: bool = False,
) -> Iterator[LlmCallContext]:
    """Выставить контекст вызова LLM на время блока.

    Args:
        operation: ``commentary`` | ``chat`` | ``warmup`` | ``judge``.
        actor_id: пользователь-инициатор (`users.id`).
        game_id: партия, по которой вызвана LLM (`games.id`).
        commentary_id: комментарий, если он уже известен (обычно NULL:
            генерируется до INSERT).
        prompt_slug: slug промпта (например, уровень ассистента).
        prompt_version: версия промпта; если ``None`` — recorder посмотрит
            `prompts.version` по `prompt_slug`.
        cache_hit: был ли это ответ из кэша (для LLM-вызовов всегда False).
    """
    context = LlmCallContext(
        operation=operation,
        actor_id=actor_id,
        game_id=game_id,
        commentary_id=commentary_id,
        prompt_slug=prompt_slug,
        prompt_version=prompt_version,
        cache_hit=cache_hit,
    )
    token = _current_call.set(context)
    try:
        yield context
    finally:
        _current_call.reset(token)


async def stream_in_call_context[T](
    stream: AsyncIterator[T],
    *,
    operation: str,
    actor_id: int | None = None,
    game_id: int | None = None,
    commentary_id: int | None = None,
    prompt_slug: str | None = None,
    prompt_version: int | None = None,
    cache_hit: bool = False,
) -> AsyncIterator[T]:
    """Обернуть SSE-генератор в контекст вызова LLM.

    Нужен stream-эндпоинтам: `with llm_call_context(...)` в самом хендлере
    не сработает — генератор исполняется после возврата ответа, поэтому
    контекст выставляется внутри обёртки вокруг каждой итерации.
    """
    with llm_call_context(
        operation=operation,
        actor_id=actor_id,
        game_id=game_id,
        commentary_id=commentary_id,
        prompt_slug=prompt_slug,
        prompt_version=prompt_version,
        cache_hit=cache_hit,
    ):
        async for item in stream:
            yield item


def record_llm_call(
    *,
    provider: str,
    model: str,
    status: CallStatus,
    latency_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    error_code: str | None = None,
    level: str | None = None,
    cache_hit: bool | None = None,
) -> None:
    """Записать вызов LLM в `llm_calls` (fire-and-forget, best-effort).

    Вызывается клиентом LLM в `finally`. Не бросает исключений и не делает
    I/O в текущем запросе: INSERT выполняется фоновой задачей в отдельной
    сессии. Вне контекста вызова/HTTP trace-контекста — no-op (скрипты и
    юнит-тесты не пишут в БД).

    Args:
        provider: провайдер (`lmstudio`/`deepseek`/...).
        model: имя модели.
        status: ``ok`` | ``error`` | ``timeout`` | ``invalid``.
        latency_ms: время вызова в миллисекундах.
        tokens_in/tokens_out: расход токенов (для stream — оценка).
        cost_usd: стоимость вызова по прайсу на момент вызова.
        error_code: машинный код ошибки (`llm_timeout`, HTTP-статус и т.п.).
        level: уровень ассистента (claim «prompt_slug = level»).
        cache_hit: был ли ответ из кэша; ``None`` — взять из контекста.
    """
    try:
        context = _current_call.get()
        trace_id = current_trace_id()
        if context is None and trace_id is None:
            return
        resolved_trace_id = (
            UUID(trace_id) if trace_id is not None else new_trace_context().trace_id
        )
        # Свой span на каждый вызов LLM: в одном запросе их может быть
        # несколько (retry комментария, sync+stream) — UNIQUE(trace, span)
        # не должен отбрасывать вторую попытку.
        record = _LlmCallRecord(
            trace_id=resolved_trace_id,
            span_id=secrets.token_hex(8),
            request_id=current_request_id(),
            context=context,
            provider=provider,
            model=model,
            level=level,
            status=status,
            latency_ms=max(0, latency_ms),
            tokens_in=max(0, tokens_in),
            tokens_out=max(0, tokens_out),
            cost_usd=Decimal(str(max(0.0, cost_usd))),
            cache_hit=(
                cache_hit
                if cache_hit is not None
                else (context.cache_hit if context else False)
            ),
            error_code=error_code,
        )
        task = asyncio.create_task(
            _persist_call(record), name=f"llm-call-{record.span_id}"
        )
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        # KI-046: запоминаем последний trace/span — по ним attach_commentary_id
        # найдёт строки этого вызова (в т.ч. вне HTTP, где trace генерируется).
        _last_trace_id.set(record.trace_id)
        _last_span_id.set(record.span_id)
    except Exception as exc:  # best-effort: LLMOps не ломает запрос
        logger.warning("llm_call_schedule_failed", error=str(exc))


async def wait_for_pending_llm_calls(timeout_s: float = 2.0) -> None:
    """Дождаться фоновых записей (тесты/корректное завершение).

    Без этого фоновые задачи могут быть отменены при закрытии event loop
    (TestClient), и тест не увидит строку в БД.

    Задача-вызыватель исключается: `attach_commentary_id` тоже живёт в
    `_pending` и ждёт записи изнутри себя (иначе был бы self-deadlock).
    """
    current = asyncio.current_task()
    tasks = [
        task
        for task in list(_pending)
        if task is not current and not task.done()
    ]
    if not tasks:
        return
    await asyncio.wait(tasks, timeout=timeout_s)


def record_cache_hit(
    *,
    latency_ms: int,
    provider: str = "cache",
    model: str = "semantic-cache",
) -> None:
    """Записать попадание в semantic cache как «вызов» LLM (KI-046).

    Кэш-ответ не идёт через LLM-клиент, поэтому без этой записи такие
    запросы полностью выпадали бы из `llm_calls`. Метрики:
    ``provider="cache"``, ``model="semantic-cache"``, 0 токенов/стоимости,
    ``status="ok"``, ``cache_hit=True``. Контекст (operation, actor_id,
    game_id, prompt_slug/version) берётся из текущего
    :func:`llm_call_context`, trace_id — из HTTP-контекста (или fallback
    recorder-а). `latency_ms` — время lookup кэша, замеряет вызывающий.
    """
    record_llm_call(
        provider=provider,
        model=model,
        status="ok",
        latency_ms=latency_ms,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        cache_hit=True,
    )


def attach_commentary_id(commentary_id: int) -> None:
    """Привязать записи `llm_calls` текущего trace к созданному комментарию.

    `commentary_id` появляется только после INSERT `commentaries`, то есть
    позже вызова LLM. Хелпер запускает фоновую задачу: дождаться INSERT-ов
    recorder-а этого trace (запись тоже fire-and-forget) и выполнить
    ``UPDATE llm_calls SET commentary_id = ... WHERE trace_id = ... AND
    commentary_id IS NULL``.

    Почему «все NULL-строки трейса», а не одна: в одном запросе бывает
    несколько вызовов (retry комментария, stream). Последний `span_id`,
    сохранённый recorder-ом в contextvar, используется для точечного
    UPDATE-а, второй проход добивает остальные NULL-строки. Вне
    trace-контекста (скрипты/юнит-тесты) — no-op; ошибки только логируются.
    """
    try:
        trace_id = _current_trace_uuid()
        if trace_id is None:
            return
        task = asyncio.create_task(
            _attach_commentary_id(trace_id, commentary_id),
            name=f"llm-call-attach-{commentary_id}",
        )
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception as exc:  # best-effort: LLMOps не ломает запрос
        logger.warning("llm_call_attach_schedule_failed", error=str(exc))


def _current_trace_uuid() -> UUID | None:
    """Trace id текущего запроса либо последний trace, записанный recorder-ом."""
    trace_id = current_trace_id()
    if trace_id is not None:
        try:
            return UUID(trace_id)
        except ValueError:
            return None
    return _last_trace_id.get()


async def _attach_commentary_id(trace_id: UUID, commentary_id: int) -> None:
    """Дождаться INSERT-ов и проставить commentary_id (best-effort)."""
    try:
        # Recorder пишет INSERT-ы в отдельных задачах: дождёмся их, иначе
        # UPDATE увидит пустую таблицу и комментарий останется без метрик.
        await wait_for_pending_llm_calls()
        async with SessionLocal() as session:
            # 1) точечно — последний вызов этого trace (contextvar recorder-а);
            last_span_id = _last_span_id.get()
            updated = 0
            if last_span_id is not None:
                result = await session.execute(
                    update(LlmCall)
                    .where(
                        LlmCall.trace_id == trace_id,
                        LlmCall.span_id == last_span_id,
                        LlmCall.commentary_id.is_(None),
                    )
                    .values(commentary_id=commentary_id)
                )
                updated += int(getattr(result, "rowcount", 0) or 0)
            # 2) добиваем остальные NULL-строки трейса (retry/stream — у них
            # свои span_id, но комментарий у всего запроса один).
            result = await session.execute(
                update(LlmCall)
                .where(
                    LlmCall.trace_id == trace_id,
                    LlmCall.commentary_id.is_(None),
                )
                .values(commentary_id=commentary_id)
            )
            updated += int(getattr(result, "rowcount", 0) or 0)
            await session.commit()
        logger.info(
            "llm_call_commentary_attached",
            trace_id=str(trace_id),
            commentary_id=commentary_id,
            span_id=last_span_id,
            rows=updated,
        )
    except Exception as exc:
        logger.warning(
            "llm_call_attach_failed",
            error=str(exc),
            commentary_id=commentary_id,
            trace_id=str(trace_id),
        )


async def _persist_call(record: _LlmCallRecord) -> None:
    """INSERT записи в отдельной сессии; ошибки — только в лог."""
    try:
        async with SessionLocal() as session:
            row = LlmCall(
                trace_id=record.trace_id,
                span_id=record.span_id,
                request_id=record.request_id,
                actor_id=_actor_id(record),
                game_id=record.context.game_id if record.context else None,
                commentary_id=(
                    record.context.commentary_id if record.context else None
                ),
                operation=(
                    record.context.operation
                    if record.context is not None
                    else _DEFAULT_OPERATION
                ),
                provider=record.provider,
                model=record.model,
                level=_level_value(record.level),
                prompt_slug=_prompt_slug(record),
                prompt_version=await _prompt_version(session, record),
                tokens_in=record.tokens_in,
                tokens_out=record.tokens_out,
                cost_usd=record.cost_usd,
                latency_ms=record.latency_ms,
                cache_hit=record.cache_hit,
                status=record.status,
                error_code=record.error_code,
            )
            session.add(row)
            await session.commit()
    except Exception as exc:
        logger.warning(
            "llm_call_record_failed",
            error=str(exc),
            operation=record.context.operation if record.context else None,
            model=record.model,
        )


def _actor_id(record: _LlmCallRecord) -> int | None:
    """Пользователь-инициатор: из контекста (вне контекста — NULL)."""
    return record.context.actor_id if record.context is not None else None


def _prompt_slug(record: _LlmCallRecord) -> str | None:
    """Slug промпта: явный из контекста, иначе уровень ассистента."""
    if record.context is not None and record.context.prompt_slug is not None:
        return record.context.prompt_slug
    return record.level


async def _prompt_version(
    session: AsyncSession, record: _LlmCallRecord
) -> int | None:
    """Версия промпта: из контекста, иначе `prompts.version` по slug-у."""
    if record.context is not None and record.context.prompt_version is not None:
        return record.context.prompt_version
    slug = _prompt_slug(record)
    if slug not in _LEVEL_VALUES:
        return None
    version = await session.scalar(
        select(Prompt.version).where(Prompt.level == slug)
    )
    return int(version) if version is not None else None


def _level_value(level: str | None) -> Level | None:
    """Строка уровня → enum `level` (неизвестное значение — NULL)."""
    if level in _LEVEL_VALUES:
        return Level(level)
    return None
