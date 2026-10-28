"""Формат Server-Sent Events для стриминга комментария и чата."""

from __future__ import annotations

import json
from typing import Any

__all__ = ["sse_event"]


def sse_event(event: str, **payload: Any) -> dict[str, str]:
    """Собрать событие для `EventSourceResponse`."""
    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False),
    }
