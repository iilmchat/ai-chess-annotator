"""Системные промпты трёх уровней (T-014).

Промпты лежат в `.txt`-файлах рядом с этим модулем — их можно править без
пересборки кода и сравнивать в диффе (RULES: «промпты версионируются»).

Загрузчик кеширует прочитанный текст и падает `InvalidLevelError` на
неизвестном уровне, чтобы опечатка не превращалась в «пустой промпт».
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from app.errors import InvalidLevelError

__all__ = ["PROMPT_LEVELS", "load_system_prompt"]

#: Уровни, для которых есть файлы промптов.
PROMPT_LEVELS = frozenset({"beginner", "club", "gm"})


@lru_cache(maxsize=len(PROMPT_LEVELS))
def load_system_prompt(level: str) -> str:
    """Прочитать системный промпт уровня.

    Args:
        level: ``beginner`` / ``club`` / ``gm``.

    Returns:
        Текст промпта без хвостовых переводов строк.

    Raises:
        InvalidLevelError: неизвестный уровень.
        FileNotFoundError: файл промпта отсутствует в пакете.
    """
    if level not in PROMPT_LEVELS:
        expected = ", ".join(sorted(PROMPT_LEVELS))
        raise InvalidLevelError(
            f"Нет промпта для уровня {level!r}; ожидается один из: {expected}"
        )
    resource = resources.files("app.prompts").joinpath(f"{level}.txt")
    return resource.read_text(encoding="utf-8").strip()
