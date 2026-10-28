"""Формат Server-Sent Events для стриминга комментария и чата."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["chunk_text", "sse_event"]


def chunk_text(text: str, size: int = 120) -> list[str]:
    """Разбить готовый текст на чанки для SSE-события `delta`."""
    if size < 1:
        raise ValueError("size должен быть ≥ 1")
    return [text[index : index + size] for index in range(0, len(text), size)]


def sse_event(event: str, **payload: Any) -> dict[str, str]:
    """Собрать событие для `EventSourceResponse`."""
    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False),
    }
