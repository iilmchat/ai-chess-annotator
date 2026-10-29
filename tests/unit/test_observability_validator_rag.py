"""OTel-спаны валидатора и RAG (T-030b).

Проверяем ``validator.validate`` и ``rag.retrieve``:

* имена и наличие спанов на публичных входах;
* атрибуты только про counts/valid/top_k/results и `trace.id` — без текста
  LLM, FEN и аннотаций (PII-free);
* статус OK/ERROR (ERROR — исключение внутри спана);
* `trace.id` из contextvars T-030 (`app.tracing.trace_id_for_span`) имеет
  приоритет над `trace_id` активного OTel-спана.

Провайдер локальный (`InMemorySpanExporter`), глобальный не трогаем — как в
`tests/unit/test_observability.py`. Модули получают tracer через
`monkeypatch` их импортированного `get_tracer`, поэтому тесты не зависят от
`OTEL_ENABLED` и порядка запуска.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import app.engine.validator as validator_module
import app.rag.service as rag_module
from app.engine.validator import validate_llm_response, validate_moves
from app.errors import InvalidFenError
from app.rag.embedder import HashEmbedder
from app.rag.service import find_similar_positions
from app.tracing import (
    new_trace_context,
    trace_context,
    trace_id_for_span,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _instrument(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> tuple[TracerProvider, InMemorySpanExporter]:
    """Подменить `get_tracer` модуля трейсером с in-memory экспортёром."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(module, "get_tracer", lambda: tracer)
    return provider, exporter


def _only_span(exporter: InMemorySpanExporter) -> Any:
    spans = exporter.get_finished_spans()
    assert len(spans) == 1, f"ожидался один спан, получено {len(spans)}"
    return spans[0]


def _hex_trace_id(span: Any) -> str:
    return format(span.context.trace_id, "032x")


# --- Валидатор ------------------------------------------------------------


def test_validate_moves_span_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, exporter = _instrument(monkeypatch, validator_module)
    try:
        result = validate_moves(["d4", "Nf6"], START)
    finally:
        provider.shutdown()

    assert result == (True, [])

    span = _only_span(exporter)
    assert span.name == "validator.validate"
    attrs = span.attributes or {}
    assert attrs["validator.moves"] == 2
    assert attrs["validator.valid"] is True
    assert attrs["validator.illegal_count"] == 0
    assert attrs["trace.id"] == _hex_trace_id(span)
    assert span.status.status_code.name == "OK"
    # Без PII: только счётчики/флаги и trace.id, никакого текста.
    assert set(attrs) == {
        "validator.moves",
        "validator.valid",
        "validator.illegal_count",
        "trace.id",
    }


def test_validate_moves_span_counts_illegal_but_keeps_ok_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нелегальный ход — легитимный результат валидации, а не ERROR."""
    provider, exporter = _instrument(monkeypatch, validator_module)
    try:
        legal, illegal = validate_moves(["Ke8"], START)
    finally:
        provider.shutdown()

    assert (legal, illegal) == (False, ["Ke8"])
    span = _only_span(exporter)
    attrs = span.attributes or {}
    assert attrs["validator.valid"] is False
    assert attrs["validator.illegal_count"] == 1
    assert span.status.status_code.name == "OK"


def test_validate_moves_span_error_on_invalid_fen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = _instrument(monkeypatch, validator_module)
    try:
        with pytest.raises(InvalidFenError):
            validate_moves(["e4"], "not-a-fen")
    finally:
        provider.shutdown()

    span = _only_span(exporter)
    assert span.status.status_code.name == "ERROR"
    attrs = span.attributes or {}
    assert attrs["validator.moves"] == 1
    # Результата валидации нет — флаги не выставляются, текст FEN не пишется.
    assert "validator.valid" not in attrs
    assert all(not isinstance(value, str) or value == _hex_trace_id(span)
               for value in attrs.values())


async def test_validate_llm_response_creates_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Публичный async-вход валидатора тоже покрыт спаном (через validate_moves)."""
    provider, exporter = _instrument(monkeypatch, validator_module)
    try:
        legal, illegal = await validate_llm_response(
            "После 1. e4 e5 2. Nf3 чёрные ответили Nc6.", START
        )
    finally:
        provider.shutdown()

    assert (legal, illegal) == (True, [])
    span = _only_span(exporter)
    assert span.name == "validator.validate"
    attrs = span.attributes or {}
    assert attrs["validator.moves"] == 4
    assert attrs["validator.valid"] is True


def test_trace_id_from_context_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-030: trace_id из contextvars важнее trace_id текущего OTel-спана."""
    provider, exporter = _instrument(monkeypatch, validator_module)
    monkeypatch.setattr(
        validator_module, "trace_id_for_span", lambda: "ctx-trace-id"
    )
    try:
        validate_moves(["d4"], START)
    finally:
        provider.shutdown()

    span = _only_span(exporter)
    attrs = span.attributes or {}
    assert attrs["trace.id"] == "ctx-trace-id"
    assert attrs["trace.id"] != _hex_trace_id(span)


def test_trace_id_for_span_prefers_real_contextvars() -> None:
    """`trace_id_for_span` реально берёт UUID из `trace_context`."""
    context = new_trace_context()
    with trace_context(context):
        assert trace_id_for_span() == str(context.trace_id)


# --- RAG ------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Минимальная заглушка AsyncSession: только `scalars(...).all()`."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def scalars(self, _statement: Any) -> _FakeScalars:
        return _FakeScalars(self._rows)


def _rag_row(annotation: str | None) -> Any:
    return SimpleNamespace(
        annotation_text=annotation,
        white="White",
        black="Black",
        event="Event",
    )


async def test_find_similar_positions_span_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = _instrument(monkeypatch, rag_module)
    rows = [_rag_row("аннотация 1"), _rag_row("аннотация 2")]
    try:
        texts = await find_similar_positions(
            START,
            top_k=2,
            session=_FakeSession(rows),  # type: ignore[arg-type]
            embedder=HashEmbedder(),
        )
    finally:
        provider.shutdown()

    assert texts == ["аннотация 1", "аннотация 2"]

    span = _only_span(exporter)
    assert span.name == "rag.retrieve"
    attrs = span.attributes or {}
    assert attrs["rag.top_k"] == 2
    assert attrs["rag.results"] == 2
    assert attrs["trace.id"] == _hex_trace_id(span)
    assert span.status.status_code.name == "OK"
    # Без PII: ни FEN, ни текстов аннотаций в атрибутах.
    assert set(attrs) == {"rag.top_k", "rag.results", "trace.id"}


async def test_find_similar_positions_span_error_on_invalid_fen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, exporter = _instrument(monkeypatch, rag_module)
    try:
        with pytest.raises(InvalidFenError):
            await find_similar_positions(
                "not-a-fen",
                session=_FakeSession([]),  # type: ignore[arg-type]
                embedder=HashEmbedder(),
            )
    finally:
        provider.shutdown()

    span = _only_span(exporter)
    assert span.status.status_code.name == "ERROR"
    attrs = span.attributes or {}
    assert attrs["rag.top_k"] == 3
    assert "rag.results" not in attrs
