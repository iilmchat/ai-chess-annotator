"""OpenTelemetry: настройка трейсинга (T-030, частично).

Задача итерации 16 — базовый трейсинг вызовов LLM, без отдельного
коллектора и durable-таблицы `llm_calls` (это следующие шаги T-030).
Поэтому:

* при ``OTEL_ENABLED=false`` (дефолт) провайдер не создаётся, а спаны в
  клиенте остаются no-op — накладных расходов нет;
* ``OTEL_EXPORTER=console`` печатает спаны в stdout (удобно для smoke);
* ``OTEL_EXPORTER=otlp`` отправляет их в OTLP/HTTP-коллектор.

Сырые промпты и ответы в атрибуты **не** попадают: только модель,
провайдер, уровень, токены, стоимость и статус.
"""

from __future__ import annotations

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from app.config import Settings

__all__ = ["configure_telemetry", "get_tracer", "shutdown_telemetry"]

logger = structlog.get_logger(__name__)


def configure_telemetry(settings: Settings) -> TracerProvider | None:
    """Настроить глобальный `TracerProvider`, если трейсинг включён.

    Returns:
        Провайдер (его нужно закрыть в lifespan) или ``None``, если
        ``OTEL_ENABLED=false``.
    """
    if not settings.otel_enabled:
        logger.info("otel_disabled")
        return None

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": "0.1.0",
                "deployment.environment": settings.app_env,
            }
        )
    )
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
    )
    return provider


def get_tracer() -> trace.Tracer:
    """Трейсер приложения (no-op, пока провайдер не настроен)."""
    return trace.get_tracer("app.llm")


def shutdown_telemetry(provider: TracerProvider | None) -> None:
    """Сбросить буферы спанов и освободить ресурсы экспортёра."""
    if provider is None:
        return
    provider.shutdown()
    logger.info("otel_shutdown")
