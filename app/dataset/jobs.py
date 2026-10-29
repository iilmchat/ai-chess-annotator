"""Фоновая выгрузка датасета LoRA (KI-050, итерация 31).

Зачем: на больших объёмах `GET /v1/admin/exports/edits.jsonl` держит
HTTP-соединение и браузер в ожидании стрима. Джоба делает то же самое в
фоне:

1. `create_export_job` пишет строку `export_jobs` со статусом ``queued``;
2. `run_export_job` (BackgroundTasks) переводит её в ``running``, пишет
   JSONL во временный файл в `EXPORT_DIR` (вне web-root), считает sha256 и
   размер, затем атомарно переименовывает и ставит ``done``;
3. при ошибке — ``failed`` + текст ошибки (обрезанный), файл не остаётся;
4. в `audit_logs` пишется `dataset.export` с `job_id`, фильтрами и числом
   строк — как у streaming-экспорта (KI-050).

Запись в файл идёт батчами через `asyncio.to_thread`, чтобы не блокировать
event loop (RULES §3.1). Джоба не переживает рестарт процесса: упавший
воркер оставит строку в ``running`` — это ограничение зафиксировано в
отчёте итерации (очередь arq/повторный запуск — следующий шаг).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import append_audit
from app.config import Settings, get_settings
from app.dataset.records import (
    ExportFilters,
    iter_export_records,
    parse_filters,
)
from app.db.models import ExportJob, ExportJobStatus
from app.db.session import SessionLocal
from app.tracing import current_trace_id, new_trace_context, trace_context

__all__ = [
    "audit_dataset_export",
    "create_export_job",
    "export_directory",
    "export_file_path",
    "get_export_job",
    "list_export_jobs",
    "resolve_export_path",
    "run_export_job",
]

logger = structlog.get_logger(__name__)

#: Размер буфера записи (байт): реже переключаемся в поток — меньше накладных.
_WRITE_BUFFER_BYTES = 64 * 1024

#: Максимальная длина `export_jobs.error` (чтобы не хранить километровый traceback).
_MAX_ERROR_CHARS = 1_000


def export_directory(settings: Settings | None = None) -> Path:
    """Каталог готовых файлов (`EXPORT_DIR`), создаётся при необходимости."""
    cfg = settings or get_settings()
    path = Path(cfg.export_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_file_path(job_id: int, settings: Settings | None = None) -> Path:
    """Путь файла джобы: ``<EXPORT_DIR>/edits-<id>.jsonl``.

    Имя строится только из числового id — пользовательский ввод в путь не
    попадает (path traversal невозможен).
    """
    return export_directory(settings) / f"edits-{int(job_id)}.jsonl"


def resolve_export_path(
    job: ExportJob, settings: Settings | None = None
) -> Path | None:
    """Проверить, что `job.file_path` лежит внутри `EXPORT_DIR`.

    Защита от подмены пути в БД: download-эндпоинт не должен отдавать
    произвольный файл хоста.
    """
    if not job.file_path:
        return None
    try:
        path = Path(job.file_path).resolve()
    except OSError:
        return None
    root = export_directory(settings)
    if not path.is_relative_to(root):
        logger.error(
            "export_job_path_outside_root",
            job_id=job.id,
            file_path=job.file_path,
            root=str(root),
        )
        return None
    return path


async def create_export_job(
    session: AsyncSession,
    *,
    actor: str,
    filters: ExportFilters,
) -> ExportJob:
    """Создать строку джобы в статусе ``queued`` и закоммитить её."""
    job = ExportJob(
        status=ExportJobStatus.queued.value,
        actor=actor,
        filters=filters.to_dict(),
    )
    session.add(job)
    await session.commit()
    # Серверные default-ы (`created_at`, `status`, `rows_exported`) нужны
    # для ответа API сразу после INSERT.
    await session.refresh(job)
    return job


async def get_export_job(
    session: AsyncSession, job_id: int
) -> ExportJob | None:
    """Загрузить джобу по id (``None`` — нет такой)."""
    return await session.get(ExportJob, job_id)


async def list_export_jobs(
    session: AsyncSession, *, limit: int = 20
) -> list[ExportJob]:
    """Последние джобы (для админки/поллинга), новые первыми."""
    rows = await session.scalars(
        select(ExportJob).order_by(ExportJob.id.desc()).limit(limit)
    )
    return list(rows)


async def run_export_job(
    job_id: int, settings: Settings | None = None
) -> None:
    """Выполнить джобу: JSONL → файл, обновить статус и аудит.

    Вызывается через FastAPI BackgroundTasks после ответа `POST /jobs`.
    Все ошибки ловятся: джоба помечается ``failed``, исключение наружу не
    летит (иначе оно уронило бы фоновую задачу ASGI).
    """
    cfg = settings or get_settings()
    trace = new_trace_context()
    exported = 0
    filters = ExportFilters()
    actor = "unknown"
    temp_path: Path | None = None
    try:
        async with SessionLocal() as session:
            now = datetime.now(UTC)
            await session.execute(
                update(ExportJob)
                .where(
                    ExportJob.id == job_id,
                    ExportJob.status == ExportJobStatus.queued.value,
                )
                .values(
                    status=ExportJobStatus.running.value, started_at=now
                )
            )
            await session.commit()
            job = await session.get(ExportJob, job_id)
            if job is None:
                logger.warning("export_job_missing", job_id=job_id)
                return
            if job.status != ExportJobStatus.running.value:
                # Джоба уже выполнена/идёт другим воркером — не дублируем.
                logger.info(
                    "export_job_skipped", job_id=job_id, status=job.status
                )
                return
            filters = parse_filters(job.filters)
            actor = job.actor
            path = export_file_path(job_id, cfg)
            temp_path = path.with_name(path.name + ".tmp")
            current_temp = temp_path

            with trace_context(trace):
                digest = hashlib.sha256()
                exported = await _write_jsonl(
                    session, filters, current_temp, digest
                )
            size = await asyncio.to_thread(
                lambda: current_temp.stat().st_size
            )
            await asyncio.to_thread(os.replace, current_temp, path)
            temp_path = None
            await session.execute(
                update(ExportJob)
                .where(ExportJob.id == job_id)
                .values(
                    status=ExportJobStatus.done.value,
                    file_path=str(path),
                    file_size=size,
                    sha256=digest.hexdigest(),
                    rows_exported=exported,
                    finished_at=datetime.now(UTC),
                    error=None,
                )
            )
            await session.commit()
        await audit_dataset_export(
            actor=actor,
            filters=filters,
            rows=exported,
            completed=True,
            job_id=job_id,
            trace_id=trace.trace_id_str,
        )
        logger.info(
            "export_job_done",
            job_id=job_id,
            rows=exported,
            size=size,
        )
    except Exception as exc:
        logger.exception("export_job_failed", job_id=job_id, error=str(exc))
        if temp_path is not None:
            await _remove_file(temp_path)
        await _mark_failed(job_id, exc)
        await audit_dataset_export(
            actor=actor,
            filters=filters,
            rows=exported,
            completed=False,
            job_id=job_id,
            trace_id=trace.trace_id_str,
        )


async def _write_jsonl(
    session: AsyncSession,
    filters: ExportFilters,
    path: Path,
    digest: Any,
) -> int:
    """Записать записи датасета в файл, обновляя sha256; вернуть число строк."""
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    handle = await asyncio.to_thread(path.open, "wb")
    exported = 0
    buffer = bytearray()
    try:
        async for record in iter_export_records(session, filters):
            line = (
                json.dumps(record, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            buffer.extend(line)
            digest.update(line)
            exported += 1
            if len(buffer) >= _WRITE_BUFFER_BYTES:
                await asyncio.to_thread(handle.write, bytes(buffer))
                buffer.clear()
        if buffer:
            await asyncio.to_thread(handle.write, bytes(buffer))
        await asyncio.to_thread(handle.flush)
    finally:
        await asyncio.to_thread(handle.close)
    return exported


async def _mark_failed(job_id: int, exc: Exception) -> None:
    """Перевести джобу в ``failed`` (best-effort, отдельная сессия)."""
    try:
        async with SessionLocal() as session:
            await session.execute(
                update(ExportJob)
                .where(ExportJob.id == job_id)
                .values(
                    status=ExportJobStatus.failed.value,
                    error=f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS],
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()
    except Exception as db_exc:
        logger.warning(
            "export_job_mark_failed_error",
            job_id=job_id,
            error=str(db_exc),
        )


async def _remove_file(path: Path) -> None:
    """Удалить временный файл, не роняя джобу."""
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError as exc:
        logger.warning(
            "export_job_temp_remove_failed", path=str(path), error=str(exc)
        )


async def audit_dataset_export(
    *,
    actor: str,
    filters: ExportFilters,
    rows: int,
    completed: bool,
    job_id: int | None = None,
    trace_id: str | None = None,
) -> None:
    """Записать `dataset.export` в `audit_logs` (best-effort, KI-050).

    Общий путь для streaming- и фоновой выгрузки: те же фильтры, число
    фактически записанных строк и `trace_id`; у джобы дополнительно
    ``job_id`` и ``background=true``.

    Args:
        trace_id: явный trace id фоновой джобы. У джобы `trace_context`
            уже закрыт к моменту аудита, поэтому contextvar пуст; без
            параметра аудит потерял бы корреляцию.
    """
    details: dict[str, Any] = {
        **filters.to_dict(),
        "rows": rows,
        "completed": completed,
        "trace_id": trace_id or current_trace_id(),
    }
    if job_id is not None:
        details["job_id"] = job_id
        details["background"] = True
    try:
        async with SessionLocal() as session:
            await append_audit(
                session,
                actor=actor,
                action="dataset.export",
                target_id="edits.jsonl",
                details=details,
            )
            await session.commit()
        logger.info(
            "dataset_export_audited",
            actor=actor,
            rows=rows,
            completed=completed,
            job_id=job_id,
        )
    except Exception as exc:  # best-effort: не ломаем выгрузку
        logger.warning(
            "dataset_export_audit_failed", error=str(exc), job_id=job_id
        )
