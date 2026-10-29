"""Выборка JSONL-записей для экспорта датасета (T-034b, KI-050).

Модуль общий для streaming-эндпоинта (`GET .../edits.jsonl`) и фоновых
джоб (`POST .../jobs`): фильтры, keyset-пагинация по `edits.id` и сборка
одной записи. Раньше эта логика жила в `app.api.v1.admin_exports`; вынос
нужен, чтобы фоновая выгрузка не дублировала правила пропуска записей и
фильтрации.

Никакого HTTP здесь нет: только SQLAlchemy-сессия и модели.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commentary.service import load_analyses
from app.dataset.export import (
    DEFAULT_BATCH_SIZE,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    build_fact_sheet,
    record_from_edit,
)
from app.db.models import Commentary, Edit, Game, Level
from app.errors import InvalidLevelError, InvalidPgnError
from app.ingestion import parse_pgn

__all__ = [
    "ExportFilters",
    "apply_export_filters",
    "build_export_record",
    "iter_export_records",
    "parse_filters",
]

#: Уровни, которые принимает фильтр `level` (совпадает с enum `level`).
ExportLevel = Literal["beginner", "club", "gm"]

_LEVEL_VALUES: frozenset[str] = frozenset({"beginner", "club", "gm"})


@dataclass(frozen=True, slots=True)
class ExportFilters:
    """Снимок query-фильтров выгрузки датасета."""

    date_from: date | None = None
    date_to: date | None = None
    level: ExportLevel | None = None
    human_edited: bool | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """JSONB-представление для `export_jobs.filters`."""
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "level": self.level,
            "human_edited": self.human_edited,
            "schema_version": self.schema_version,
        }


def parse_filters(raw: Mapping[str, Any] | None) -> ExportFilters:
    """Восстановить фильтры из JSONB (неизвестные значения → дефолты).

    Битое/старое значение в `export_jobs.filters` не должно ломать
    повторный запуск: непонятные поля просто игнорируются.
    """
    data: Mapping[str, Any] = raw or {}

    def _date(key: str) -> date | None:
        value = data.get(key)
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None

    raw_level = data.get("level")
    level: ExportLevel | None = (
        raw_level if raw_level in _LEVEL_VALUES else None
    )
    raw_human = data.get("human_edited")
    human_edited: bool | None = (
        bool(raw_human) if isinstance(raw_human, bool) else None
    )
    try:
        schema_version = int(data.get("schema_version") or SCHEMA_VERSION)
    except (TypeError, ValueError):
        schema_version = SCHEMA_VERSION
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        schema_version = SCHEMA_VERSION
    return ExportFilters(
        date_from=_date("date_from"),
        date_to=_date("date_to"),
        level=level,
        human_edited=human_edited,
        schema_version=schema_version,
    )


def _day_start(day: date) -> datetime:
    """Начало суток (UTC) для фильтра по `edits.created_at`."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def apply_export_filters(
    statement: Any, filters: ExportFilters
) -> Any:
    """Наложить фильтры выгрузки на выборку `edits` (join commentaries)."""
    if filters.date_from is not None:
        statement = statement.where(
            Edit.created_at >= _day_start(filters.date_from)
        )
    if filters.date_to is not None:
        # `date_to` включительно: `< начало следующих суток`.
        statement = statement.where(
            Edit.created_at
            < _day_start(filters.date_to) + timedelta(days=1)
        )
    if filters.level is not None:
        statement = statement.where(Commentary.level == Level(filters.level))
    if filters.human_edited is not None:
        statement = statement.where(
            Commentary.human_edited.is_(filters.human_edited)
        )
    return statement


async def build_export_record(
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


async def iter_export_records(
    session: AsyncSession,
    filters: ExportFilters,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> AsyncIterator[dict[str, Any]]:
    """Стримить записи датасета keyset-пагинацией по `edits.id`.

    Проблемные записи (нет партии/PGN не разобран/нет анализа) молча
    пропускаются — как в streaming-экспорте: одна битая строка не должна
    ронять всю выгрузку.
    """
    last_id = 0
    while True:
        statement = (
            select(Edit, Commentary, Game)
            .join(Commentary, Edit.commentary_id == Commentary.id)
            .outerjoin(Game, Commentary.game_id == Game.id)
            .where(Edit.id > last_id)
            .order_by(Edit.id)
            .limit(batch_size)
        )
        statement = apply_export_filters(statement, filters)
        rows = (await session.execute(statement)).all()
        if not rows:
            return
        for row in rows:
            edit: Edit = row[0]
            commentary: Commentary = row[1]
            game: Game | None = row[2]
            last_id = edit.id
            record = await build_export_record(
                session,
                edit,
                commentary,
                game,
                schema_version=filters.schema_version,
            )
            if record is not None:
                yield record
