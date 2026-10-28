"""Оценка числа токенов для stream-ответов без usage.

OpenAI-совместимые стримы не всегда возвращают `usage`; для записи в
`commentaries` нужна приблизительная оценка. Эвристика `chars / 4`
используется только в SSE-ветке, где точные токены недоступны.
"""

from __future__ import annotations

__all__ = ["estimate_tokens"]


def estimate_tokens(text: str) -> int:
    """Грубая оценка токенов по длине текста (не для биллинга)."""
    if not text:
        return 0
    return max(1, len(text) // 4)
