"""JSONL-экспорт датасета правок для админ-панели (T-034b).

Эндпоинт живёт под API-префиксом (`/v1/admin/exports/edits.jsonl`), но вне
смонтированного `/admin` (starlette-admin), поэтому не получает
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

Чтение — keyset-пагинацией по `edits.id` (батчами), без загрузки всей
таблицы в память; пустой результат — 200 с пустым телом.

Аудит (KI-050, итерация 29): после завершения генератора пишется запись
`audit_logs.action="dataset.export"` с фильтрами, числом фактически
выгруженных строк и trace_id. Запись — best-effort в отдельной сессии:
сбой аудита не ломает уже отданный стрим (только warning в лог).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import ADMIN_SESSION_COOKIE, decode_admin_session
from app.audit import append_audit
from app.auth.tokens import decode_token
from app.commentary.service import load_analyses
from app.config import Settings, get_settings
from app.dataset.export import (
    DEFAULT_BATCH_SIZE,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_fact_sheet,
    record_from_edit,
)
from app.db.models import AdminRole, Commentary, Edit, Game, Level, User
from app.db.session import SessionLocal, get_session
from app.errors import (
    AuthError,
    ForbiddenError,
    InvalidLevelError,
    InvalidPgnError,
)
from app.ingestion import parse_pgn
from app.tracing import current_trace_id

__all__ = ["router"]

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/exports", tags=["admin-exports"])

#: Роли, которым доступна выгрузка датасета.
_EXPORT_ROLES: frozenset[AdminRole] = frozenset(
    {AdminRole.admin, AdminRole.auditor}
)

#: Уровни, которые принимает фильтр `level`.
ExportLevel = Literal["beginner", "club", "gm"]
#: Версии схемы JSONL: 1 — legacy rewrite, 2 — пакет фактов.
ExportSchemaVersion = Literal[1, 2]

#: `schema_version` по умолчанию (совпадает с `scripts.export_dataset`).
_DEFAULT_SCHEMA_VERSION: ExportSchemaVersion = 2


async def _require_export_actor(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """JWT admin/auditor или подписанная cookie-сессия админки.

    Returns:
        Строка-актор для логов (``jwt:<email>`` / ``session:<username>``).

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
        return f"jwt:{user.email}"

    cookie = request.cookies.get(ADMIN_SESSION_COOKIE)
    if cookie:
        session_payload = decode_admin_session(settings, cookie)
        if session_payload is not None:
            admin_user = session_payload.get("admin_user")
            if (
                isinstance(admin_user, str)
                and admin_user == settings.admin_username
            ):
                return f"session:{admin_user}"

    raise AuthError("Требуется авторизация администратора")


def _day_start(day: date) -> datetime:
    """Начало суток (UTC) для фильтра по `edits.created_at`."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _apply_filters(
    statement: Any,
    *,
    date_from: date | None,
    date_to: date | None,
    level: ExportLevel | None,
    human_edited: bool | None,
) -> Any:
    """Наложить фильтры query на выборку `edits` (join commentaries)."""
    if date_from is not None:
        statement = statement.where(Edit.created_at >= _day_start(date_from))
    if date_to is not None:
        # `date_to` включительно: `< начало следующих суток`.
        statement = statement.where(
            Edit.created_at < _day_start(date_to) + timedelta(days=1)
        )
    if level is not None:
        statement = statement.where(Commentary.level == Level(level))
    if human_edited is not None:
        statement = statement.where(
            Commentary.human_edited.is_(human_edited)
        )
    return statement


async def _record_for_edit(
    session: AsyncSession,
    edit: Edit,
    commentary: Commentary,
    game: Game | None,
    *,
    schema_version: int,
) -> dict[str, Any] | None:
    """Собрать JSONL-запись или вернуть ``None`` (пропуск проблемной записи)."""
    if schema_version == LEGACY_SCHEMA_VERSION:
        return record_from_edit(
            edit, commentary, schema_version=LEGACY_SCHEMA_VERSION
        )
    if game is None:
        return None
    try:
        parsed = parse_pgn(game.pgn)
    except InvalidPgnError:
        return None
    analyses = await load_analyses(session, game, parsed)
    if not analyses:
        return None
    try:
        fact_sheet = build_fact_sheet(
            parsed, analyses, commentary.level.value
        )
    except (InvalidLevelError, ValueError):
        return None
    return record_from_edit(
        edit,
        commentary,
        schema_version=SCHEMA_VERSION,
        fact_package=fact_sheet,
    )


async def _audit_dataset_export(
    *,
    actor: str,
    date_from: date | None,
    date_to: date | None,
    level: ExportLevel | None,
    human_edited: bool | None,
    schema_version: int,
    rows: int,
    completed: bool,
) -> None:
    """Записать факт выгрузки датасета в `audit_logs` (KI-050).

    Вызывается после завершения (или обрыва) стрима, в отдельной сессии:
    основная транзакция запроса уже не нужна, а аудит не должен ломать
    скачивание. Фильтры пишутся как есть (без PII), ``rows`` — число
    фактически отданных строк (v2 может пропускать проблемные записи).
    """
    try:
        async with SessionLocal() as session:
            await append_audit(
                session,
                actor=actor,
                action="dataset.export",
                target_id="edits.jsonl",
                details={
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "level": level,
                    "human_edited": human_edited,
                    "schema_version": schema_version,
                    "rows": rows,
                    "completed": completed,
                    "trace_id": current_trace_id(),
                },
            )
            await session.commit()
        logger.info(
            "dataset_export_audited",
            actor=actor,
            rows=rows,
            completed=completed,
        )
    except Exception as exc:  # best-effort: не ломаем уже отданный стрим
        logger.warning("dataset_export_audit_failed", error=str(exc))


async def _iter_edits_jsonl(
    *,
    date_from: date | None,
    date_to: date | None,
    level: ExportLevel | None,
    human_edited: bool | None,
    schema_version: int,
    actor: str,
) -> AsyncIterator[bytes]:
    """Стримить NDJSON батчами (keyset по `edits.id`) и аудировать выгрузку."""
    last_id = 0
    exported = 0
    completed = False
    try:
        async with SessionLocal() as session:
            while True:
                statement = (
                    select(Edit, Commentary, Game)
                    .join(Commentary, Edit.commentary_id == Commentary.id)
                    .outerjoin(Game, Commentary.game_id == Game.id)
                    .where(Edit.id > last_id)
                    .order_by(Edit.id)
                    .limit(DEFAULT_BATCH_SIZE)
                )
                statement = _apply_filters(
                    statement,
                    date_from=date_from,
                    date_to=date_to,
                    level=level,
                    human_edited=human_edited,
                )
                rows = (await session.execute(statement)).all()
                if not rows:
                    completed = True
                    return
                for row in rows:
                    edit: Edit = row[0]
                    commentary: Commentary = row[1]
                    game: Game | None = row[2]
                    last_id = edit.id
                    record = await _record_for_edit(
                        session,
                        edit,
                        commentary,
                        game,
                        schema_version=schema_version,
                    )
                    if record is None:
                        continue
                    exported += 1
                    yield (
                        json.dumps(record, ensure_ascii=False) + "\n"
                    ).encode("utf-8")
    finally:
        # completed=False — клиент оборвал соединение/ошибка: фиксируем
        # факт частичной выгрузки, а не теряем его.
        await _audit_dataset_export(
            actor=actor,
            date_from=date_from,
            date_to=date_to,
            level=level,
            human_edited=human_edited,
            schema_version=schema_version,
            rows=exported,
            completed=completed,
        )


@router.get("/edits.jsonl")
async def export_edits_jsonl(
    request: Request,
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
    logger.info(
        "dataset_export_stream",
        date_from=str(date_from) if date_from else None,
        date_to=str(date_to) if date_to else None,
        level=level,
        human_edited=human_edited,
        schema_version=schema_version,
    )
    return StreamingResponse(
        _iter_edits_jsonl(
            date_from=date_from,
            date_to=date_to,
            level=level,
            human_edited=human_edited,
            schema_version=schema_version,
            actor=_actor,
        ),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="edits.jsonl"',
            "Cache-Control": "no-store",
        },
    )
