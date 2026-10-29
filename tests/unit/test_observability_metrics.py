"""OTLP-метрики LLM (T-109b).

Проверяем:

* `configure_metrics` выключен без `OTEL_ENABLED`/`OTEL_METRICS_ENABLED`;
* `record_llm_metrics` без провайдера — no-op (не падает);
* с локальным `MeterProvider` + `InMemoryMetricReader` счётчики
  `llm.calls`/`llm.tokens`/`llm.cost.usd` и гистограмма `llm.latency`
  получают ожидаемые значения;
* cache-hit вызов не добавляет токены/стоимость.
"""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app import observability
from app.config import Settings
from app.observability import configure_metrics, record_llm_metrics


def _metric_points(
    reader: InMemoryMetricReader,
) -> dict[str, list[Any]]:
    """Собрать data points по имени метрики (для проверок)."""
    data = reader.get_metrics_data()
    assert data is not None
    result: dict[str, list[Any]] = {}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                result.setdefault(metric.name, []).extend(
                    metric.data.data_points
                )
    return result


def test_configure_metrics_disabled_without_otel() -> None:
    assert configure_metrics(Settings(otel_enabled=False)) is None


def test_configure_metrics_disabled_by_flag() -> None:
    settings = Settings(otel_enabled=True, otel_metrics_enabled=False)
    assert configure_metrics(settings) is None


def test_record_metrics_without_provider_is_noop() -> None:
    observability.reset_metrics_for_tests()
    record_llm_metrics(
        provider="lmstudio",
        model="qwen",
        operation="commentary",
        level="club",
        status="ok",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.001,
        latency_ms=100,
        cache_hit=False,
    )


def test_record_metrics_updates_instruments() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    observability._init_llm_instruments(provider.get_meter("test"))
    try:
        record_llm_metrics(
            provider="lmstudio",
            model="qwen",
            operation="commentary",
            level="club",
            status="ok",
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.0015,
            latency_ms=120,
            cache_hit=False,
        )
        points = _metric_points(reader)

        calls = points["llm.calls"]
        assert len(calls) == 1
        assert calls[0].value == 1
        assert calls[0].attributes["llm.level"] == "club"
        assert calls[0].attributes["llm.status"] == "ok"
        assert calls[0].attributes["llm.cache_hit"] is False

        tokens = {
            point.attributes["llm.direction"]: point.value
            for point in points["llm.tokens"]
        }
        assert tokens == {"in": 10, "out": 5}

        assert points["llm.cost.usd"][0].value == pytest.approx(0.0015)
        latency = points["llm.latency"]
        assert latency[0].count == 1
        assert latency[0].sum == pytest.approx(120)
    finally:
        provider.shutdown()
        observability.reset_metrics_for_tests()


def test_cache_hit_records_call_without_tokens_and_cost() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    observability._init_llm_instruments(provider.get_meter("test"))
    try:
        record_llm_metrics(
            provider="cache",
            model="semantic-cache",
            operation="commentary",
            level=None,
            status="ok",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_ms=1,
            cache_hit=True,
        )
        points = _metric_points(reader)
        assert points["llm.calls"][0].attributes["llm.cache_hit"] is True
        assert points["llm.calls"][0].attributes["llm.level"] == "unknown"
        assert "llm.tokens" not in points
        assert "llm.cost.usd" not in points
    finally:
        provider.shutdown()
        observability.reset_metrics_for_tests()
