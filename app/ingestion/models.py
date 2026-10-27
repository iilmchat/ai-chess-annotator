"""Pydantic-модели результата ingestion (T-007).

Модели — контракт между ingestion и остальными слоями: API (T-009), БД
(`games.pgn` + `games.phase_breakdown`), движок (T-008). Все модели
неизменяемы (`frozen`), лишние поля запрещены (`extra="forbid"`) — чтобы
опечатка в имени поля не проходила молча.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

__all__ = [
    "GameMetadata",
    "ParsedGame",
    "ParsedMove",
    "PhaseBreakdown",
    "PositionRepeat",
]

#: Код ECO: буква A–E и две цифры (например, ``E21``, ``B90``).
EcoCode = Annotated[str, StringConstraints(pattern=r"^[A-E]\d{2}$")]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ParsedMove(BaseModel):
    """Один полуход и позиция, которая получилась после него."""

    model_config = _FROZEN

    ply: int = Field(ge=1, description="Номер полухода, начиная с 1")
    san: str = Field(min_length=1, max_length=10, description="Ход в SAN")
    fen: str = Field(min_length=1, description="FEN позиции ПОСЛЕ хода")
    is_capture: bool = Field(description="Взятие (включая en passant)")
    is_check: bool = Field(description="Ход даёт шах")


class GameMetadata(BaseModel):
    """Метаданные партии из PGN-заголовков.

    Отсутствующие или «пустые» заголовки (``?``, ``????.??.??``) приводятся
    к ``None`` — так потребитель не отличает «нет поля» от «поле-заглушка».
    """

    model_config = _FROZEN

    white: str | None = None
    black: str | None = None
    event: str | None = None
    date: str | None = None
    result: str | None = None


class PhaseBreakdown(BaseModel):
    """Границы фаз партии в полуходах.

    * дебют — полуходы ``1 … opening_end_ply``;
    * миттельшпиль — ``opening_end_ply + 1 … endgame_start_ply - 1``
      (может быть пустым);
    * эндшпиль — ``endgame_start_ply … ply_count``.

    ``opening_end_ply = 0`` означает, что партия началась сразу с
    эндшпильной позиции (например, разбор учебного окончания).
    """

    model_config = _FROZEN

    opening_end_ply: int = Field(ge=0, description="Последний полуход дебюта")
    endgame_start_ply: int | None = Field(
        default=None,
        ge=1,
        description="Первый полуход эндшпиля; None — эндшпиля не было",
    )


class PositionRepeat(BaseModel):
    """Повтор позиции в партии (основа троекратного повтора).

    ``first_ply = 0`` означает повтор стартовой позиции.
    """

    model_config = _FROZEN

    first_ply: int = Field(ge=0)
    repeat_ply: int = Field(ge=1)
    board_fen: str = Field(description="FEN без полей счётчиков хода")


class ParsedGame(BaseModel):
    """Нормализованная партия — результат ``parse_pgn``."""

    model_config = _FROZEN

    metadata: GameMetadata
    moves: list[ParsedMove]
    eco_code: EcoCode | None = None
    phase_breakdown: PhaseBreakdown
    final_fen: str = Field(min_length=1)

    @property
    def ply_count(self) -> int:
        """Количество полуходов (не сериализуется в JSON)."""
        return len(self.moves)
