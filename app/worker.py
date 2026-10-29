"""arq-воркер фоновых задач ChessAnnotator (KI-053).

Очередь — Redis (`ARQ_QUEUE_NAME`, по умолчанию ``arq:queue``). API кладёт
задачу `run_export_job` при `POST /v1/admin/exports/jobs`, а отдельный
процесс `arq worker.WorkerSettings` забирает её и выполняет вне
API-процесса: рестарт API больше не теряет выгрузку.

Почему отдельный воркер, а не `BackgroundTasks`: задача переживает
перезапуск API-контейнера (очередь в Redis), API не тратит event loop на
тяжёлый JSONL, а воркер можно масштабировать отдельно. При старте воркер
выполняет recovery: `running`-джобы старше `EXPORT_JOB_STALE_MINUTES`
помечаются ``failed`` (см. `app.dataset.jobs.recover_stale_export_jobs`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.config import Settings, get_settings
from app.dataset.jobs import recover_stale_export_jobs
from app.dataset.jobs import run_export_job as execute_export_job

__all__ = [
    "WorkerSettings",
    "run_export_job",
    "shutdown",
    "startup",
]

#: Настройки на момент импорта: arq CLI создаёт WorkerSettings один раз.
_settings: Settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    """Подготовить контекст воркера и восстановить зависшие джобы."""
    settings = ctx.get("settings")
    if not isinstance(settings, Settings):
        settings = _settings
        ctx["settings"] = settings
    recovered = await recover_stale_export_jobs(settings)
    ctx["recovered_jobs"] = recovered


async def shutdown(ctx: dict[str, Any]) -> None:
    """Освободить ресурсы воркера (сейчас — только лог)."""
    ctx.clear()


async def run_export_job(ctx: dict[str, Any], job_id: int) -> None:
    """arq-задача: сгенерировать JSONL для `export_jobs.id`."""
    settings = ctx.get("settings")
    if not isinstance(settings, Settings):
        settings = _settings
    await execute_export_job(int(job_id), settings)


class WorkerSettings:
    """Конфигурация `arq worker.WorkerSettings` (CLI/Docker)."""

    functions: ClassVar[list[Any]] = [run_export_job]
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    queue_name = _settings.arq_queue_name
    on_startup = startup
    on_shutdown = shutdown
    #: Выгрузка большого v2-датасета может идти долго; 30 минут — потолок.
    job_timeout = 1800
    max_tries = 3
    #: Результат (None/ошибка) храним час — для диагностики через arq.
    keep_result = 3600
    health_check_interval = 60
