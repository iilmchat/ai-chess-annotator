"""OpenTelemetry: трейсинг и метрики приложения (T-030, T-109b).

Что настраивается:

* **Трейсы** — при ``OTEL_ENABLED=true``; экспортёр ``console`` печатает
  спаны в stdout, ``otlp`` отправляет их в OTLP/HTTP-коллектор (по
  умолчанию ``http://localhost:4318/v1/traces``). В docker-compose
  observability-профиля эндпоинт — ``http://otel-collector:4318``.
* **Метрики** — тот же OTLP-эндпоинт, ``/v1/metrics``; счётчики вызовов
  LLM, токенов, стоимости и гистограмма латентности (``llm.*``). Их
  записывает durable-рекордер `app.llm.calls`, поэтому цифры в Grafana
  совпадают с `llm_calls` (источник — один и тот же код).
* **HTTP-спаны** `http_request` живут в `app.tracing`; здесь только
  провайдеры/экспортёры.

Сырые промпты и ответы в атрибуты **не** попадают: только модель,
провайдер, уровень, операция, токены, стоимость и статус.

Без ``OTEL_ENABLED=true`` провайдеры не создаются, инструменты — no-op:
накладных расходов нет, метрики не пишутся.
"""

from __future__ import annotations

import structlog
from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from app.config import Settings

__all__ = [
    "configure_metrics",
    "configure_telemetry",
    "get_tracer",
    "record_llm_metrics",
    "reset_metrics_for_tests",
    "shutdown_metrics",
    "shutdown_telemetry",
]

logger = structlog.get_logger(__name__)

#: Инструменты метрик создаются после установки провайдера: инструмент,
#: созданный у no-op ProxyMeter, навсегда остаётся no-op.
_llm_calls: Counter | None = None
_llm_tokens: Counter | None = None
_llm_cost: Counter | None = None
_llm_latency: Histogram | None = None

#: Таймаут экспорта OTLP-метрик (мс).
_METRICS_EXPORT_TIMEOUT_MS = 10_000


def _resource(settings: Settings) -> Resource:
    """Общие атрибуты ресурса для трейсов и метрик."""
    return Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
            "service.namespace": "chessannotator",
            "deployment.environment": settings.app_env,
        }
    )


def configure_telemetry(settings: Settings) -> TracerProvider | None:
    """Настроить глобальный `TracerProvider`, если трейсинг включён.

    Returns:
        Провайдер (его нужно закрыть в lifespan) или ``None``, если
        ``OTEL_ENABLED=false``.
    """
    if not settings.otel_enabled:
        logger.info("otel_disabled")
        return None

    provider = TracerProvider(resource=_resource(settings))
    exporter_name = settings.otel_exporter.strip().lower()
    if exporter_name == "otlp":
        # Импорт локальный: OTLP-экспортёр не нужен для console-режима и
        # может отсутствовать в урезанных сборках.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(
            endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces"
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    logger.info(
        "otel_configured",
        exporter=exporter_name,
        service=settings.otel_service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
    )
    return provider


def configure_metrics(settings: Settings) -> SdkMeterProvider | None:
    """Настроить глобальный `MeterProvider` и инструменты `llm.*`.

    Returns:
        Провайдер (нужно закрыть в lifespan) или ``None``, если ``OTEL_ENABLED``
        или ``OTEL_METRICS_ENABLED`` выключены.
    """
    if not settings.otel_enabled or not settings.otel_metrics_enabled:
        logger.info("otel_metrics_disabled")
        return None

    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )

    exporter = OTLPMetricExporter(
        endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/metrics",
        timeout=_METRICS_EXPORT_TIMEOUT_MS / 1000,
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=settings.otel_metrics_interval_ms,
        export_timeout_millis=_METRICS_EXPORT_TIMEOUT_MS,
    )
    provider = SdkMeterProvider(
        resource=_resource(settings), metric_readers=[reader]
    )
    metrics.set_meter_provider(provider)
    _init_llm_instruments(metrics.get_meter("app.llm"))
    logger.info(
        "otel_metrics_configured",
        endpoint=settings.otel_exporter_otlp_endpoint,
        interval_ms=settings.otel_metrics_interval_ms,
    )
    return provider


def _init_llm_instruments(meter: metrics.Meter) -> None:
    """Создать счётчики/гистограмму LLM (после установки провайдера)."""
    global _llm_calls, _llm_tokens, _llm_cost, _llm_latency
    _llm_calls = meter.create_counter(
        "llm.calls",
        unit="1",
        description="Вызовы LLM по модели/уровню/статусу",
    )
    _llm_tokens = meter.create_counter(
        "llm.tokens",
        unit="1",
        description="Израсходованные токены (direction=in|out)",
    )
    _llm_cost = meter.create_counter(
        # Единица пустая: OTel-экспортёр Prometheus иначе добавит суффикс
        # `_USD` (`llm_cost_usd_USD_total`), а дашборд ждёт
        # `llm_cost_usd_total`. Валюта уже в имени метрики.
        "llm.cost.usd",
        unit="",
        description="Стоимость вызовов LLM",
    )
    _llm_latency = meter.create_histogram(
        "llm.latency",
        unit="ms",
        description="Латентность вызова LLM",
    )


def record_llm_metrics(
    *,
    provider: str,
    model: str,
    operation: str,
    level: str | None,
    status: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: int,
    cache_hit: bool,
) -> None:
    """Записать метрики одного вызова LLM (no-op без MeterProvider).

    Вызывается из `app.llm.calls.record_llm_call` — там же, где пишется
    durable-строка `llm_calls`, поэтому Grafana и админ-дашборд считают одно
    и то же.
    """
    if _llm_calls is None:
        return
    attributes: dict[str, str | bool] = {
        "llm.provider": provider,
        "llm.model": model,
        "llm.operation": operation,
        "llm.level": level or "unknown",
        "llm.status": status,
    }
    try:
        _llm_calls.add(1, {**attributes, "llm.cache_hit": cache_hit})
        if tokens_in > 0:
            _llm_tokens.add(  # type: ignore[union-attr]
                tokens_in, {**attributes, "llm.direction": "in"}
            )
        if tokens_out > 0:
            _llm_tokens.add(  # type: ignore[union-attr]
                tokens_out, {**attributes, "llm.direction": "out"}
            )
        if cost_usd > 0:
            _llm_cost.add(  # type: ignore[union-attr]
                cost_usd, attributes
            )
        if latency_ms >= 0:
            _llm_latency.record(  # type: ignore[union-attr]
                latency_ms, attributes
            )
    except Exception as exc:  # best-effort: метрики не ломают запрос
        logger.warning("otel_metrics_record_failed", error=str(exc))


def reset_metrics_for_tests() -> None:
    """Сбросить глобальные инструменты (изоляция тестов)."""
    global _llm_calls, _llm_tokens, _llm_cost, _llm_latency
    _llm_calls = None
    _llm_tokens = None
    _llm_cost = None
    _llm_latency = None


def get_tracer(name: str = "app.llm") -> trace.Tracer:
    """Трейсер приложения (no-op, пока провайдер не настроен).

    Args:
        name: имя инструментария (`app.llm` для клиента, `app.http` для
            trace-middleware) — попадает в `InstrumentationScope` спанов.
    """
    return trace.get_tracer(name)


def shutdown_telemetry(provider: TracerProvider | None) -> None:
    """Сбросить буферы спанов и освободить ресурсы экспортёра."""
    if provider is None:
        return
    provider.shutdown()
    logger.info("otel_shutdown")


def shutdown_metrics(provider: SdkMeterProvider | None) -> None:
    """Сбросить буферы метрик (force flush) и освободить ресурсы."""
    reset_metrics_for_tests()
    if provider is None:
        return
    provider.shutdown()
    logger.info("otel_metrics_shutdown")
