"""JSONL-экспорт датасета правок для админ-панели (T-034b, KI-050).

Эндпоинты живут под API-префиксом (`/v1/admin/exports/...`), но вне
смонтированного `/admin` (starlette-admin), поэтому не получают
`request.session`. Авторизация — два пути:

* **JWT** access-токен роли ``admin``/``auditor`` (``Authorization: Bearer``)
  — для скриптов и curl; роль ``user`` → 403, битый/просроченный → 401;
* **cookie `admin_session`** — чтобы кнопка «Экспорт датасета» из админки
  работала без ручного Bearer: cookie ставит `/admin/login`, формат
  разбирает `app.admin.decode_admin_session` (тот же `itsdangerous`-секрет,
  что у SessionMiddleware админки).

Формат — NDJSON, по строке на правку. ``schema_version=2`` (по умолчанию)
переиспользует функции `scripts.export_dataset`: system-промпт уровня,
``user`` = пакет фактов (`build_fact_sheet`), ``assistant`` = текст правки.
``schema_version=1`` — legacy-пара ``original_text → edited_text``.
Проблемные записи (нет партии/PGN не разобран/нет анализа) в v2
пропускаются, стрим не падает.

Два режима выгрузки (KI-050):

* ``GET /edits.jsonl`` — streaming для небольших объёмов (keyset-пагинация
  по `edits.id`, без загрузки всей таблицы в память);
* ``POST /jobs`` → ``GET /jobs/{id}`` → ``GET /jobs/{id}/download`` —
  фоновая джоба для больших объёмов: браузер не ждёт стрим, файл лежит в
  `EXPORT_DIR` (вне web-root), статусы — в `export_jobs`. Создавать джобы
  может только роль ``admin``/legacy-сессия; ``auditor`` читает список и
  скачивает готовые файлы (RBAC, KI-045).

Аудит: и стрим, и джоба пишут `audit_logs.action="dataset.export"` с
фильтрами, числом строк и trace_id (best-effort, `app.dataset.jobs`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import ADMIN_SESSION_COOKIE, decode_admin_session
from app.auth.tokens import decode_token
from app.config import Settings, get_settings
from app.dataset.export import SCHEMA_VERSION
from app.dataset.jobs import (
    audit_dataset_export,
    create_export_job,
    get_export_job,
    list_export_jobs,
    mark_export_job_failed,
    resolve_export_path,
)
from app.dataset.records import ExportFilters, ExportLevel, iter_export_records
from app.db.models import AdminRole, ExportJob, ExportJobStatus, User
from app.db.session import SessionLocal, get_session
from app.errors import (
    AuthError,
    ExportJobFileMissingError,
    ExportJobNotReadyError,
    ExportQueueUnavailableError,
    ForbiddenError,
    NotFoundError,
)

__all__ = ["router"]

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/exports", tags=["admin-exports"])

#: Роли, которым доступна выгрузка датасета.
_EXPORT_ROLES: frozenset[AdminRole] = frozenset(
    {AdminRole.admin, AdminRole.auditor}
)

#: `schema_version` по умолчанию (совпадает с `scripts.export_dataset`).
_DEFAULT_SCHEMA_VERSION = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ExportActor:
    """Кто выполняет выгрузку: имя для логов + роль для RBAC."""

    name: str
    role: str

    @property
    def can_write(self) -> bool:
        """Создавать фоновые джобы может admin/legacy, но не auditor."""
        return self.role != AdminRole.auditor.value


async def _authorize_export(
    request: Request,
    session: AsyncSession,
    settings: Settings,
) -> ExportActor:
    """JWT admin/auditor или подписанная cookie-сессия админки.

    Raises:
        AuthError: нет/битые креденшелы (→ 401).
        ForbiddenError: валидный JWT роли ``user`` (→ 403).
    """
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, raw_token = header.partition(" ")
        token = raw_token.strip()
        if scheme.lower() != "bearer" or not token:
            raise AuthError(
                "Требуется заголовок Authorization: Bearer <access-token>"
            )
        claims = decode_token(token, settings, expected_type="access")
        user = await session.get(User, claims.user_id)
        if user is None or not user.is_active:
            raise AuthError("Пользователь не найден или отключён")
        if user.role not in _EXPORT_ROLES:
            raise ForbiddenError(
                "Экспорт датасета доступен только admin и auditor"
            )
        return ExportActor(name=f"jwt:{user.email}", role=user.role.value)

    cookie = request.cookies.get(ADMIN_SESSION_COOKIE)
    if cookie:
        session_payload = decode_admin_session(settings, cookie)
        if session_payload is not None:
            admin_user = session_payload.get("admin_user")
            if (
                isinstance(admin_user, str)
                and admin_user == settings.admin_username
            ):
                return ExportActor(
                    name=f"session:{admin_user}", role="legacy"
                )

    raise AuthError("Требуется авторизация администратора")


async def _require_export_actor(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Актор с правом чтения выгрузки (admin/auditor/legacy)."""
    actor = await _authorize_export(request, session, settings)
    return actor.name


async def _require_export_writer(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Актор с правом создания джобы; auditor — read-only (403)."""
    actor = await _authorize_export(request, session, settings)
    if not actor.can_write:
        raise ForbiddenError(
            "Создавать фоновые экспорты может только роль admin"
        )
    return actor.name


class ExportJobCreateRequest(BaseModel):
    """Тело `POST /v1/admin/exports/jobs` — те же фильтры, что у стрима."""

    date_from: date | None = None
    date_to: date | None = None
    level: ExportLevel | None = None
    human_edited: bool | None = None
    schema_version: int = Field(
        default=_DEFAULT_SCHEMA_VERSION,
        ge=1,
        le=2,
        description="1 — legacy rewrite, 2 — пакет фактов",
    )


class ExportJobResponse(BaseModel):
    """Состояние фоновой выгрузки + ссылка на скачивание."""

    id: int
    status: ExportJobStatus
    actor: str
    filters: dict[str, Any]
    rows_exported: int
    file_size: int | None = None
    sha256: str | None = None
    error: str | None = None
    download_url: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


def _job_response(job: ExportJob, settings: Settings) -> ExportJobResponse:
    """ORM → ответ API (download_url только для готовой джобы)."""
    done = job.status == ExportJobStatus.done.value
    return ExportJobResponse(
        id=job.id,
        status=ExportJobStatus(job.status),
        actor=job.actor,
        filters=dict(job.filters or {}),
        rows_exported=job.rows_exported,
        file_size=job.file_size,
        sha256=job.sha256,
        error=job.error,
        download_url=(
            f"{settings.api_prefix}/admin/exports/jobs/{job.id}/download"
            if done
            else None
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def _iter_edits_jsonl(
    *,
    filters: ExportFilters,
    actor: str,
) -> AsyncIterator[bytes]:
    """Стримить NDJSON батчами и аудировать выгрузку после завершения."""
    exported = 0
    completed = False
    try:
        async with SessionLocal() as session:
            async for record in iter_export_records(session, filters):
                exported += 1
                yield (
                    json.dumps(record, ensure_ascii=False) + "\n"
                ).encode("utf-8")
        completed = True
    finally:
        # completed=False — клиент оборвал соединение/ошибка: фиксируем
        # факт частичной выгрузки, а не теряем его.
        await audit_dataset_export(
            actor=actor,
            filters=filters,
            rows=exported,
            completed=completed,
        )


@router.get("/edits.jsonl")
async def export_edits_jsonl(
    _actor: Annotated[str, Depends(_require_export_actor)],
    date_from: Annotated[
        date | None, Query(description="Нижняя граница edits.created_at")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Верхняя граница edits.created_at")
    ] = None,
    level: Annotated[
        ExportLevel | None, Query(description="Уровень комментария")
    ] = None,
    human_edited: Annotated[
        bool | None, Query(description="Только правки человека")
    ] = None,
    schema_version: Annotated[
        int,
        Query(
            ge=1,
            le=2,
            description="1 — legacy rewrite, 2 — пакет фактов",
        ),
    ] = _DEFAULT_SCHEMA_VERSION,
) -> StreamingResponse:
    """Скачать датасет правок в NDJSON (`edits.jsonl`)."""
    filters = ExportFilters(
        date_from=date_from,
        date_to=date_to,
        level=level,
        human_edited=human_edited,
        schema_version=schema_version,
    )
    logger.info(
        "dataset_export_stream",
        date_from=str(date_from) if date_from else None,
        date_to=str(date_to) if date_to else None,
        level=level,
        human_edited=human_edited,
        schema_version=schema_version,
    )
    return StreamingResponse(
        _iter_edits_jsonl(filters=filters, actor=_actor),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="edits.jsonl"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/jobs", status_code=202, response_model=ExportJobResponse
)
async def create_export_job_endpoint(
    payload: ExportJobCreateRequest,
    request: Request,
    _actor: Annotated[str, Depends(_require_export_writer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExportJobResponse:
    """Создать фоновую выгрузку и поставить её в очередь arq (202).

    Очередь — Redis; API не выполняет выгрузку сам (KI-053). Если Redis
    недоступен, джоба помечается ``failed`` и клиент получает 503
    ``export_queue_unavailable`` — статус не остаётся «вечно queued».
    """
    filters = ExportFilters(
        date_from=payload.date_from,
        date_to=payload.date_to,
        level=payload.level,
        human_edited=payload.human_edited,
        schema_version=payload.schema_version,
    )
    job = await create_export_job(session, actor=_actor, filters=filters)
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        await mark_export_job_failed(job.id, "очередь arq недоступна")
        raise ExportQueueUnavailableError(
            "Очередь фоновых задач недоступна (Redis/arq)"
        )
    try:
        # _job_id делает постановку идемпотентной при повторе HTTP-запроса.
        queued = await pool.enqueue_job(
            "run_export_job", job.id, _job_id=f"export-job:{job.id}"
        )
    except Exception as exc:
        await mark_export_job_failed(
            job.id, f"не удалось поставить в очередь: {exc}"
        )
        logger.warning(
            "dataset_export_enqueue_failed", job_id=job.id, error=str(exc)
        )
        raise ExportQueueUnavailableError(
            "Не удалось поставить задачу в очередь"
        ) from exc
    logger.info(
        "dataset_export_job_created",
        job_id=job.id,
        actor=_actor,
        queued=queued is not None,
        **filters.to_dict(),
    )
    return _job_response(job, settings)


@router.get("/jobs", response_model=list[ExportJobResponse])
async def list_export_jobs_endpoint(
    _actor: Annotated[str, Depends(_require_export_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ExportJobResponse]:
    """Последние фоновые выгрузки (новые первыми)."""
    jobs = await list_export_jobs(
        session, limit=settings.export_jobs_page_size
    )
    return [_job_response(job, settings) for job in jobs]


@router.get("/jobs/{job_id}", response_model=ExportJobResponse)
async def get_export_job_endpoint(
    job_id: Annotated[int, Path(gt=0, description="ID джобы экспорта")],
    _actor: Annotated[str, Depends(_require_export_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExportJobResponse:
    """Статус фоновой выгрузки (поллинг из админки)."""
    job = await get_export_job(session, job_id)
    if job is None:
        raise NotFoundError(f"Джоба экспорта {job_id} не найдена")
    return _job_response(job, settings)


@router.get("/jobs/{job_id}/download")
async def download_export_job(
    job_id: Annotated[int, Path(gt=0, description="ID джобы экспорта")],
    _actor: Annotated[str, Depends(_require_export_actor)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    """Скачать готовый JSONL джобы.

    Raises:
        NotFoundError: джобы нет (→ 404).
        ExportJobNotReadyError: статус queued/running/failed (→ 409).
        ExportJobFileMissingError: файл удалён/путь вне `EXPORT_DIR` (→ 410).
    """
    job = await get_export_job(session, job_id)
    if job is None:
        raise NotFoundError(f"Джоба экспорта {job_id} не найдена")
    if job.status == ExportJobStatus.failed.value:
        raise ExportJobNotReadyError(
            f"Джоба завершилась ошибкой: {job.error or 'неизвестно'}"
        )
    if job.status != ExportJobStatus.done.value:
        raise ExportJobNotReadyError(
            f"Экспорт ещё не готов (status={job.status})"
        )
    path = resolve_export_path(job, settings)
    if path is None or not path.is_file():
        raise ExportJobFileMissingError(
            f"Файл джобы {job_id} не найден в EXPORT_DIR"
        )
    logger.info(
        "dataset_export_job_download",
        job_id=job.id,
        rows=job.rows_exported,
        size=job.file_size,
    )
    return FileResponse(
        path,
        media_type="application/x-ndjson",
        filename=f"edits-{job.id}.jsonl",
        headers={
            "Cache-Control": "no-store",
            "X-Export-Sha256": job.sha256 or "",
            "X-Export-Rows": str(job.rows_exported),
        },
    )
