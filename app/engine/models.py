"""Pydantic-модели результатов движка (T-008).

Соглашения:

* оценки (`score_cp`, `eval_before`, `eval_after`) — **сантипешки**;
* `EngineLine` — строка MultiPV из UCI: оценка **с точки зрения стороны,
  которая делает ход** в анализируемой позиции (так их отдаёт Stockfish);
* `MoveAnalysis` — анализ полухода: `eval_before`/`eval_after` уже приведены
  **к точке зрения игрока, сделавшего ход** (плюс = хорошо для него), чтобы
  `delta_cp = eval_before - eval_after` читался как «сколько потерял»;
* мат кодируется в сантипешках как ``±(10000 − 100·N)`` — иначе мат нельзя
  сравнить с обычной оценкой и посчитать дельту.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "MATE_CP_BASE",
    "EngineLine",
    "MoveAnalysis",
    "MoveClassification",
]

#: База для перевода мата в сантипешки (мат в 1 → 9900, в 5 → 9500).
MATE_CP_BASE: int = 10_000

#: Классы хода по SPEC.md §2.2. ``brilliant`` пока не выставляется (T-011).
MoveClassification = Literal[
    "best", "brilliant", "good", "inaccuracy", "mistake", "blunder"
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")

_UCI_MOVE_PATTERN = r"^[a-h][1-8][a-h][1-8][qrbn]?$"


class EngineLine(BaseModel):
    """Одна линия MultiPV из ответа Stockfish."""

    model_config = _FROZEN

    multipv: int = Field(ge=1, description="Ранг линии в MultiPV (1 — лучшая)")
    depth: int = Field(ge=0, description="Глубина, на которой получена линия")
    score_cp: int | None = Field(
        default=None, description="Оценка в сантипешках (если не мат)"
    )
    mate: int | None = Field(
        default=None, description="Мат в N ходов (если найден); знак — за кого"
    )
    move_uci: str = Field(pattern=_UCI_MOVE_PATTERN, description="Первый ход линии")
    pv: list[str] = Field(default_factory=list, description="Principal variation в UCI")

    @model_validator(mode="after")
    def _exactly_one_score(self) -> EngineLine:
        """В линии должна быть ровно одна форма оценки: cp или мат."""
        if (self.score_cp is None) == (self.mate is None):
            raise ValueError("нужна ровно одна оценка: score_cp или mate")
        return self


class MoveAnalysis(BaseModel):
    """Анализ одного полухода: что сыграно, что было лучше и насколько хуже."""

    model_config = _FROZEN

    ply: int = Field(ge=1, description="Номер полухода (1-based)")
    played_move_san: str = Field(min_length=1)
    played_move_uci: str = Field(pattern=_UCI_MOVE_PATTERN)
    eval_before: int = Field(
        description="Оценка позиции до хода, с точки зрения сделавшего ход"
    )
    eval_after: int = Field(
        description="Оценка позиции после хода, с точки зрения сделавшего ход"
    )
    best_move_uci: str = Field(pattern=_UCI_MOVE_PATTERN)
    best_move_san: str = Field(min_length=1)
    delta_cp: int = Field(ge=0, description="Потеря в сантипешках (≥ 0)")
    classification: MoveClassification
    engine_depth: int | None = Field(
        default=None, description="Глубина лучшей линии в позиции до хода"
    )
    mate_after: int | None = Field(
        default=None,
        description=(
            "Мат в N ходов в позиции после хода (знак — за кого, с точки "
            "зрения белых); None — мата нет"
        ),
    )
