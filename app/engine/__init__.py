"""Engine Service: анализ позиций и партий через Stockfish (T-008…T-011).

Модуль не знает про HTTP и БД: на вход — FEN или история ходов из ingestion,
на выходе — неизменяемые Pydantic-модели (`EngineLine`, `MoveAnalysis`) и
решение Policy Layer (`choose_recommended_move`).
"""

from __future__ import annotations

from app.engine.models import EngineLine, MoveAnalysis, MoveClassification
from app.engine.policy import CLUB_EQUAL_LINE_CP, choose_recommended_move
from app.engine.service import (
    ENGINE_PROFILES,
    EngineProfile,
    EngineService,
    resolve_profile,
)

__all__ = [
    "CLUB_EQUAL_LINE_CP",
    "ENGINE_PROFILES",
    "EngineLine",
    "EngineProfile",
    "EngineService",
    "MoveAnalysis",
    "MoveClassification",
    "choose_recommended_move",
    "resolve_profile",
]
