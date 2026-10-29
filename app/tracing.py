"""W3C trace-контекст HTTP-запросов (T-030).

Зачем отдельный модуль, а не `observability.py`: OTel-настройка — это
экспортёры и провайдер, а здесь — жизненный цикл трейс-контекста одного
запроса (traceparent, contextvars, заголовок ответа). Смешивать их значило
бы связать бизнес-код с деталями SDK.

Контракт:

* входящий `traceparent` (`00-<32hex>-<16hex>-<flags>`) парсится по W3C;
  отсутствующий/битый/нулевой заменяется новым `uuid4` (trace) и
  случайным 16-hex (span) — запрос **не** отклоняется;
* `request.state.trace_id` — UUID строкой с дефисами (для БД и заголовка
  `X-Trace-Id`), `request.state.span_id` — 16 hex;
* те же значения доступны через contextvars (`current_trace_id` и др.) —
  их читают recorder `app.llm.calls` и логи;
* в ответ всегда добавляется `X-Trace-Id`;
* при включённом OTel (`OTEL_ENABLED=true`) запрос получает span
  `http_request`, чей **trace id равен** W3C trace id, поэтому LLM-спаны
  вложены в него и коррелируют с `llm_calls.trace_id`. При выключенном
  OTel провайдер — no-op, накладных расходов нет.

Тексты запросов/ответов и заголовок `Authorization` в трейс не попадают.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4

import structlog
from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    ProxyTracerProvider,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability import get_tracer

__all__ = [
    "TraceContext",
    "TraceMiddleware",
    "current_request_id",
    "current_span_id",
    "current_trace_id",
    "new_trace_context",
    "parse_traceparent",
    "request_span",
    "trace_context",
    "trace_id_for_span",
    "traceparent_header",
]

logger = structlog.get_logger(__name__)

#: W3C trace-context: version 00, 32 hex trace id, 16 hex parent/span id,
#: 2 hex flags. Версии кроме 00 по спеке должны игнорироваться.
_TRACEPARENT_RE = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
    re.IGNORECASE,
)

#: Максимальная длина принимаемого `X-Request-Id` (колонка VARCHAR(64)).
_MAX_REQUEST_ID_LEN = 64

#: Заголовок запроса/ответа с идентификатором трейса.
TRACE_HEADER = "X-Trace-Id"
#: Заголовок W3C trace-context.
TRACEPARENT_HEADER = "traceparent"
#: Необязательный клиентский идентификатор запроса.
REQUEST_ID_HEADER = "X-Request-Id"

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Trace-контекст запроса: W3C-идентификаторы и request id."""

    trace_id: UUID
    span_id: str
    flags: str = "01"
    request_id: str | None = None

    @property
    def trace_id_str(self) -> str:
        """UUID с дефисами — формат БД (`llm_calls.trace_id`) и заголовка."""
        return str(self.trace_id)

    @property
    def traceparent(self) -> str:
        """Полный W3C `traceparent` для текущего контекста."""
        return traceparent_header(self.trace_id, self.span_id, self.flags)


def traceparent_header(trace_id: UUID, span_id: str, flags: str = "01") -> str:
    """Собрать `traceparent` версии 00 (sampled по умолчанию)."""
    return f"00-{trace_id.hex}-{span_id.lower()}-{flags.lower()}"


def parse_traceparent(raw: str | None) -> TraceContext | None:
    """Разобрать W3C `traceparent`; ``None`` — если заголовок невалиден.

    Нулевые trace/span id по спеке считаются невалидными (не «новый
    трейс», а именно мусор) — вызывающий сгенерирует свой контекст.
    """
    if not raw:
        return None
    match = _TRACEPARENT_RE.match(raw.strip())
    if match is None:
        return None
    trace_hex, span_hex, flags = match.groups()
    if trace_hex == "0" * 32 or span_hex == "0" * 16:
        return None
    return TraceContext(
        trace_id=UUID(hex=trace_hex.lower()),
        span_id=span_hex.lower(),
        flags=flags.lower(),
    )


def new_trace_context(request_id: str | None = None) -> TraceContext:
    """Новый trace-контекст (uuid4 + случайный 16-hex span id)."""
    return TraceContext(
        trace_id=uuid4(),
        span_id=secrets.token_hex(8),
        request_id=request_id,
    )


def current_trace_id() -> str | None:
    """Trace id текущего запроса (UUID с дефисами) или ``None`` вне HTTP."""
    return _trace_id_var.get()


def current_span_id() -> str | None:
    """Span id текущего запроса (16 hex) или ``None`` вне HTTP."""
    return _span_id_var.get()


def current_request_id() -> str | None:
    """`X-Request-Id` текущего запроса, если клиент его прислал."""
    return _request_id_var.get()


def trace_id_for_span() -> str | None:
    """`trace_id` для атрибутов спана: UUID из contextvars или hex OTel.

    Используется сервисами без HTTP-контекста (validator/rag): если запрос
    прошёл через `TraceMiddleware`, возвращается UUID с дефисами; иначе —
    hex-id активного OTel-спана (или ``None``).
    """
    current = current_trace_id()
    if current:
        return current
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        return format(span_context.trace_id, "032x")
    return None


@contextmanager
def trace_context(context: TraceContext) -> Iterator[TraceContext]:
    """Выставить trace-контекст в contextvars и structlog на время блока.

    Используется middleware-ом; отдельно полезен тестам и фоновым задачам,
    которые должны писать `trace_id` в `llm_calls` вне HTTP-запроса.
    """
    tokens = [
        _trace_id_var.set(context.trace_id_str),
        _span_id_var.set(context.span_id),
        _request_id_var.set(context.request_id),
    ]
    structlog.contextvars.bind_contextvars(
        trace_id=context.trace_id_str, span_id=context.span_id
    )
    try:
        yield context
    finally:
        structlog.contextvars.unbind_contextvars("trace_id", "span_id")
        _request_id_var.reset(tokens[2])
        _span_id_var.reset(tokens[1])
        _trace_id_var.reset(tokens[0])


class TraceMiddleware:
    """ASGI-middleware: trace-контекст запроса и заголовок `X-Trace-Id`.

    Регистрируется **самым внешним** (в `create_app` — последним), чтобы
    `X-Trace-Id` был и у ответов других middleware (429/404), и у SSE.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _header(scope, REQUEST_ID_HEADER)
        if request_id is not None:
            request_id = request_id.strip()[:_MAX_REQUEST_ID_LEN] or None
        context = parse_traceparent(_header(scope, TRACEPARENT_HEADER))
        if context is None:
            context = new_trace_context(request_id=request_id)
        elif request_id is not None:
            context = TraceContext(
                trace_id=context.trace_id,
                span_id=context.span_id,
                flags=context.flags,
                request_id=request_id,
            )

        # request.state.* у Starlette — это и есть scope["state"].
        state: dict[str, object] = scope.setdefault("state", {})
        state["trace_id"] = context.trace_id_str
        state["span_id"] = context.span_id
        state["traceparent"] = context.traceparent
        if context.request_id is not None:
            state["request_id"] = context.request_id

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[TRACE_HEADER] = context.trace_id_str
            await send(message)

        with trace_context(context), request_span(context):
            await self.app(scope, receive, send_with_trace)


def _header(scope: Scope, name: str) -> str | None:
    """Значение заголовка (case-insensitive) из ASGI-scope."""
    wanted = name.lower().encode("latin-1")
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for header_name, value in headers:
        if header_name.lower() == wanted:
            return value.decode("latin-1")
    return None


@contextmanager
def request_span(
    context: TraceContext, *, tracer: trace.Tracer | None = None
) -> Iterator[None]:
    """Span `http_request` с trace id из W3C-контекста (no-op без OTel).

    Args:
        context: trace-контекст запроса.
        tracer: явный трейсер (тесты с локальным провайдером); по умолчанию
            берётся глобальный, а при выключенном OTel span не создаётся.
    """
    active_tracer = tracer
    if active_tracer is None:
        if isinstance(trace.get_tracer_provider(), ProxyTracerProvider):
            # OTEL_ENABLED=false: провайдер не настроен, спаны не создаём.
            yield
            return
        active_tracer = get_tracer("app.http")

    parent = SpanContext(
        trace_id=int(context.trace_id.hex, 16),
        span_id=int(context.span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(int(context.flags, 16)),
    )
    parent_context = set_span_in_context(NonRecordingSpan(parent))
    with active_tracer.start_as_current_span(
        "http_request", context=parent_context
    ) as span:
        span.set_attribute("trace.id", context.trace_id_str)
        span.set_attribute("trace.span_id", context.span_id)
        yield
