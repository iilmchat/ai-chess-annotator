"""Мотивы: детектор и модель (T-010)."""

from __future__ import annotations

from app.motifs.detector import MAX_MOTIFS, detect_motifs, is_piece_hanging
from app.motifs.models import Motif, MotifType

__all__ = [
    "MAX_MOTIFS",
    "Motif",
    "MotifType",
    "detect_motifs",
    "is_piece_hanging",
]
