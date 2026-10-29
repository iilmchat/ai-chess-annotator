"""Переиспользуемые части экспорта датасета LoRA (T-034b/KI-039).

Модуль лежит в `app/`, чтобы API-слой (`app.api.v1.admin_exports`) не
импортировал `scripts/` — production wheel содержит только `app`, а
Docker-образ ставит пакет через `pip install .`. CLI-обёртка
(`scripts/export_dataset.py`) импортирует эти функции из `app.dataset`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from importlib import resources
from typing import Any

from app.engine.models import MoveAnalysis
from app.ingestion import ParsedGame
from app.llm.prompts import build_fact_package

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_PROMPT_LEVEL",
    "LEGACY_SCHEMA_VERSION",
    "PROMPT_LEVELS",
    "SCHEMA_VERSION",
    "SKIP_REASON_LABELS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ExportStats",
    "build_fact_sheet",
    "load_system_prompt",
    "record_from_edit",
]

#: Актуальная версия схемы: user = факт-пакет, assistant = правка.
SCHEMA_VERSION = 2
#: Legacy-схема «original_text → edited_text» (rewrite-семантика).
LEGACY_SCHEMA_VERSION = 1
#: Версии схемы, которые умеет писать экспорт.
SUPPORTED_SCHEMA_VERSIONS = (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION)
#: Сколько `edits` читать за один keyset-батч при экспорте v2.
DEFAULT_BATCH_SIZE = 100
#: Уровни, для которых есть файл system-промпта.
PROMPT_LEVELS = ("beginner", "club", "gm")
#: Fallback, если в БД встретился уровень без файла промпта.
DEFAULT_PROMPT_LEVEL = "club"

#: Причины пропуска записи при экспорте v2 → человекочитаемая подпись.
SKIP_REASON_LABELS: dict[str, str] = {
    "no_game": "нет партии (games.id не найден)",
    "invalid_pgn": "PGN не разобран (InvalidPgnError)",
    "no_analysis": "нет анализа ходов (moves пусты)",
    "invalid_level": "неизвестный уровень комментария",
}


@cache
def _read_prompt_file(level: str) -> str:
    """Прочитать `app/prompts/<level>.txt` через importlib.resources."""
    resource = resources.files("app.prompts").joinpath(f"{level}.txt")
    return resource.read_text(encoding="utf-8").strip()


def load_system_prompt(level: str) -> str:
    """System-промпт для уровня (`beginner`/`club`/`gm`).

    Читается через `importlib.resources`, чтобы работало и из wheel-пакета.
    Неизвестный уровень откатывается на `club`.
    """
    resolved = level if level in PROMPT_LEVELS else DEFAULT_PROMPT_LEVEL
    text = _read_prompt_file(resolved)
    if not text:
        raise RuntimeError(
            f"Пустой системный промпт: app/prompts/{resolved}.txt"
        )
    return text


def level_value(level: Any) -> str:
    """`commentaries.level` (enum или str) → строка уровня."""
    return level.value if hasattr(level, "value") else str(level)


@dataclass
class ExportStats:
    """Счётчики экспорта v2: сколько выгружено и почему пропущено."""

    schema_version: int = SCHEMA_VERSION
    exported: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    levels: Counter[str] = field(default_factory=Counter)
    fact_sheet_chars: int = 0

    def record_exported(self, level: str, fact_sheet: str) -> None:
        """Учесть успешно выгруженную запись."""
        self.exported += 1
        self.levels[level] += 1
        self.fact_sheet_chars += len(fact_sheet)

    def record_skipped(self, reason: str) -> None:
        """Учесть пропущенную запись (причина — ключ `SKIP_REASON_LABELS`)."""
        self.skipped[reason] += 1

    @property
    def skipped_total(self) -> int:
        """Всего пропущенных записей."""
        return sum(self.skipped.values())

    def summary_lines(self) -> list[str]:
        """Строки итоговой статистики для stdout."""
        lines = [
            f"Экспорт датасета: schema_version={self.schema_version}",
            f"Экспортировано записей: {self.exported}",
            f"Пропущено записей: {self.skipped_total}",
        ]
        for reason, count in sorted(self.skipped.items()):
            label = SKIP_REASON_LABELS.get(reason, reason)
            lines.append(f"  {label}: {count}")
        if self.levels:
            distribution = ", ".join(
                f"{level}={count}" for level, count in sorted(self.levels.items())
            )
        else:
            distribution = "нет данных"
        lines.append(f"Распределение по уровням: {distribution}")
        if self.exported:
            average = self.fact_sheet_chars // self.exported
            lines.append(
                "Размер пакета фактов: "
                f"в среднем {average} символов, всего {self.fact_sheet_chars}"
            )
        return lines


def build_fact_sheet(
    parsed_game: ParsedGame,
    analyses: Sequence[MoveAnalysis],
    level: str,
) -> str:
    """Собрать `user`-сообщение v2 — пакет фактов под уровень.

    Raises:
        ValueError: нет анализа ходов (пакет фактов без них бессмысленен).
        InvalidLevelError: неизвестный уровень.
    """
    if not analyses:
        raise ValueError(
            "нет анализа ходов: пакет фактов собрать нельзя "
            "(для партии не вызывался /v1/analyze)"
        )
    return build_fact_package(parsed_game, list(analyses), level)


def record_from_edit(
    edit: Any,
    commentary: Any,
    *,
    schema_version: int = SCHEMA_VERSION,
    system_prompt: str | None = None,
    fact_package: str | None = None,
) -> dict[str, Any]:
    """Собрать одну JSONL-строку из `Edit` и `Commentary`.

    Первым сообщением всегда идёт `system` с промптом уровня комментария
    (или явно переданным `system_prompt`).

    Args:
        edit: строка `edits` (id, commentary_id, editor_id, тексты, created_at).
        commentary: строка `commentaries` (level, model, game_id).
        schema_version: 1 — legacy «original_text → edited_text»; 2 — пакет
            фактов в `user`.
        system_prompt: переопределить system-сообщение.
        fact_package: обязателен для v2 — текст `build_fact_package(...)`.

    Raises:
        ValueError: неизвестная `schema_version` или v2 без `fact_package`.
    """
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"неизвестная schema_version={schema_version!r}; "
            f"ожидается {LEGACY_SCHEMA_VERSION} или {SCHEMA_VERSION}"
        )
    if schema_version == SCHEMA_VERSION and fact_package is None:
        raise ValueError(
            "для schema_version=2 нужен fact_package "
            "(user = пакет фактов, а не original_text)"
        )
    level = level_value(commentary.level)
    prompt = system_prompt if system_prompt is not None else load_system_prompt(level)
    created_at: datetime = edit.created_at
    if schema_version == LEGACY_SCHEMA_VERSION:
        user_content = edit.original_text
    else:
        # Ранняя проверка выше гарантирует наличие пакета фактов для v2.
        assert fact_package is not None
        user_content = fact_package
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": edit.edited_text},
    ]
    if schema_version == LEGACY_SCHEMA_VERSION:
        return {
            "messages": messages,
            "id": edit.id,
            "commentary_id": edit.commentary_id,
            "editor_id": edit.editor_id,
            "level": level,
            "model": commentary.model,
            "created_at": created_at.isoformat(),
            "schema_version": LEGACY_SCHEMA_VERSION,
        }
    assert fact_package is not None
    return {
        "messages": messages,
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "id": edit.id,
            "commentary_id": edit.commentary_id,
            "game_id": commentary.game_id,
            "editor_id": edit.editor_id,
            "level": level,
            "model": commentary.model,
            "created_at": created_at.isoformat(),
            "fact_sheet_chars": len(fact_package),
        },
    }
