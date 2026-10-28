"""Генерация комментариев: БД → факты → LLM → валидатор (T-016)."""

from __future__ import annotations

from app.commentary.service import (
    GeneratedCommentary,
    RagContextBuilder,
    generate_commentary,
    load_analyses,
    load_game,
)

__all__ = [
    "GeneratedCommentary",
    "RagContextBuilder",
    "generate_commentary",
    "load_analyses",
    "load_game",
]
