"""Engine Service: анализ позиций и партий через Stockfish (T-008).

Модуль не знает про HTTP и БД: на вход — FEN или история ходов из ingestion,
на выходе — неизменяемые Pydantic-модели (`EngineLine`, `MoveAnalysis`).
"""

from __future__ import annotations

from app.engine.models import EngineLine, MoveAnalysis, MoveClassification
from app.engine.service import (
    ENGINE_PROFILES,
    EngineProfile,
    EngineService,
    resolve_profile,
)

__all__ = [
    "ENGINE_PROFILES",
    "EngineLine",
    "EngineProfile",
    "EngineService",
    "MoveAnalysis",
    "MoveClassification",
    "resolve_profile",
]
