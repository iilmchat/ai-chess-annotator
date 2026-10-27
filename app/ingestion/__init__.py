"""Ingestion: приём и нормализация шахматных партий (T-007).

Модуль ничего не знает про БД и HTTP: на вход — текст PGN/FEN, на выходе —
неизменяемые Pydantic-модели (см. :mod:`app.ingestion.models`).
"""

from __future__ import annotations

from app.ingestion.eco import detect_eco, lookup_eco, normalize_eco
from app.ingestion.models import (
    GameMetadata,
    ParsedGame,
    ParsedMove,
    PhaseBreakdown,
    PositionRepeat,
)
from app.ingestion.parser import find_position_repeats, parse_fen, parse_pgn

__all__ = [
    "GameMetadata",
    "ParsedGame",
    "ParsedMove",
    "PhaseBreakdown",
    "PositionRepeat",
    "detect_eco",
    "find_position_repeats",
    "lookup_eco",
    "normalize_eco",
    "parse_fen",
    "parse_pgn",
]
