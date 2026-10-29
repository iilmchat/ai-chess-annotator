"""OTel-спаны LLM-клиента и trace-контекста запроса (T-030).

Провайдер локальный (с `InMemorySpanExporter`), глобальный не трогаем:
так тест не зависит от порядка запуска и не конфликтует с `OTEL_ENABLED`.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.config import Settings
from app.errors import LLMError
from app.llm.openai_client import OpenAICompatibleClient
from app.observability import configure_telemetry
from app.tracing import (
    TraceContext,
    current_request_id,
    current_span_id,
    current_trace_id,
    request_span,
    trace_context,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _client(
    handler: Handler, provider: TracerProvider
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="http://127.0.0.1:8034/v1",
        model="qwen/qwen3-4b-2507",
        api_key="lm-studio",
        provider_name="lmstudio",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        tracer=provider.get_tracer("test"),
        price_in_usd_per_1m=1.0,
        price_out_usd_per_1m=2.0,
    )


def _completion() -> dict[str, object]:
    return {
        "model": "qwen/qwen3-4b-2507",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "e4"}}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


async def test_llm_complete_span_has_usage_attributes() -> None:
    provider, exporter = _provider()
    try:
        client = _client(
            lambda request: httpx.Response(200, json=_completion()), provider
        )
        client.set_level("club")

        result = await client.complete("prompt", system_prompt="system")

        assert result.text == "e4"
        assert result.usage.cost_usd == pytest.approx(0.000026)
        spans = exporter.get_finished_spans()
        assert [span.name for span in spans] == ["llm_generate"]
        attrs = spans[0].attributes
        assert attrs is not None
        assert attrs["llm.model"] == "qwen/qwen3-4b-2507"
        assert attrs["llm.provider"] == "lmstudio"
        assert attrs["llm.level"] == "club"
        assert attrs["llm.tokens_in"] == 12
        assert attrs["llm.tokens_out"] == 7
        assert attrs["llm.cost_usd"] == pytest.approx(0.000026)
        assert attrs["llm.status"] == "ok"
    finally:
        provider.shutdown()


async def test_llm_error_span_is_marked_failed() -> None:
    provider, exporter = _provider()
    try:
        client = _client(
            lambda request: httpx.Response(400, text="bad request"), provider
        )
        client.set_level("club")

        with pytest.raises(LLMError):
            await client.complete("prompt")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = spans[0].attributes
        assert attrs is not None
        assert attrs["llm.status"] == "error"
        assert attrs["llm.error_type"] == "LLMError"
        assert spans[0].status.status_code.name == "ERROR"
    finally:
        provider.shutdown()


def test_telemetry_disabled_by_default() -> None:
    assert configure_telemetry(Settings(otel_enabled=False)) is None


# --- T-030: trace-контекст запроса ----------------------------------------


def test_trace_context_binds_and_resets_contextvars() -> None:
    context = TraceContext(
        trace_id=uuid4(), span_id="abcdef0123456789", request_id="req-42"
    )

    assert current_trace_id() is None
    with trace_context(context):
        assert current_trace_id() == context.trace_id_str
        assert current_span_id() == "abcdef0123456789"
        assert current_request_id() == "req-42"
    assert current_trace_id() is None
    assert current_span_id() is None
    assert current_request_id() is None


async def test_request_span_has_trace_id_from_w3c_context() -> None:
    """`http_request`-span: атрибут trace.id и OTel trace id из traceparent."""
    provider, exporter = _provider()
    trace_id = uuid4()
    context = TraceContext(trace_id=trace_id, span_id="0123456789abcdef")
    try:
        with request_span(context, tracer=provider.get_tracer("test")):
            with provider.get_tracer("test").start_as_current_span("child"):
                pass
        spans = {span.name: span for span in exporter.get_finished_spans()}
        request = spans["http_request"]
        attrs = request.attributes
        assert attrs is not None
        assert attrs["trace.id"] == str(trace_id)
        assert attrs["trace.span_id"] == "0123456789abcdef"
        assert request.context.trace_id == int(trace_id.hex, 16)
    finally:
        provider.shutdown()


async def test_llm_span_is_nested_under_request_span() -> None:
    """LLM-спан вложен в `http_request` и делит с ним trace id (корреляция)."""
    provider, exporter = _provider()
    trace_id = uuid4()
    context = TraceContext(trace_id=trace_id, span_id="fedcba9876543210")
    try:
        with request_span(context, tracer=provider.get_tracer("test")):
            client = _client(
                lambda request: httpx.Response(200, json=_completion()), provider
            )
            client.set_level("club")
            await client.complete("prompt")

        spans = exporter.get_finished_spans()
        assert [span.name for span in spans] == ["llm_generate", "http_request"]
        llm_span, request = spans
        assert llm_span.context.trace_id == int(trace_id.hex, 16)
        assert request.context.trace_id == int(trace_id.hex, 16)
        assert llm_span.parent is not None
        assert llm_span.parent.span_id == request.context.span_id
        attrs = request.attributes
        assert attrs is not None
        assert attrs["trace.id"] == str(trace_id)
    finally:
        provider.shutdown()

