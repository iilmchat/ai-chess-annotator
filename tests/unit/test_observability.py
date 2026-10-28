"""OTel-спаны LLM-клиента (T-030, частично).

Провайдер локальный (с `InMemorySpanExporter`), глобальный не трогаем:
так тест не зависит от порядка запуска и не конфликтует с `OTEL_ENABLED`.
"""

from __future__ import annotations

from collections.abc import Callable

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
