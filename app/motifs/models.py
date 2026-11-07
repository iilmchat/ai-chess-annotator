"""Модели мотивов (T-010).

`Motif` — то, что сохраняется в таблицу `motifs` (`type`, `description`,
`importance`) и что уходит в комментарий как «факт» для LLM.

Типы намеренно фиксированы (`Literal`): по ним потом строятся промпты и
фильтры, поэтому опечатка в строке мотива должна ломать тест, а не
прод.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Motif", "MotifType"]

#: Известные типы мотивов.
MotifType = Literal[
    "passed_pawn",
    "weak_square",
    "open_file",
    "open_diagonal",
    "king_safety",
    "fork",
    "pin",
    "mate_threat",
    "winning_capture",
    "hanging_piece",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Motif(BaseModel):
    """Один найденный мотив.

    `importance` — 1…5 (5 — критично для оценки позиции). Значение задаёт
    детектор по эвристике: близость к королю, продвинутость пешки, мат и т. п.
    """

    model_config = _FROZEN

    type: MotifType
    description: str = Field(min_length=1, max_length=200)
    importance: int = Field(ge=1, le=5)
