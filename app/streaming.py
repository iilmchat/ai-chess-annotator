"""Формат Server-Sent Events для стриминга комментария и чата.

Здесь же лежит общий буферизующий CJK-фильтр SSE (KI-060): он не даёт
китайским иероглифам попасть в `delta` ни в одном из стримов.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.commentary.semantic import count_cjk, has_cjk, strip_cjk_sentences

__all__ = [
    "MAX_DELTA_CHARS",
    "MIN_DELTA_CHARS",
    "CjkStreamBuffer",
    "chunk_text",
    "sse_event",
]

#: Конец предложения для буферизации SSE-текста (KI-060, итерация 53).
#: Латинские терминаторы требуют пробела/конца (чтобы не резать «1.20» и
#: сокращения), CJK-терминаторы (``。！？``) — безусловная граница: в
#: китайской типографике после них пробела может не быть. Закрывающие
#: кавычки/скобки входят в предложение. Тире и двоеточия границей не
#: считаются.
_SENTENCE_END = re.compile(
    r"(?:[.!?…]+[\"'»)\]]*(?=\s|$)|[。！？]+[\"'»)\]]*)"
)

#: Минимальный буфер, после которого можно резать по границе слова.
MIN_DELTA_CHARS = 20
#: Максимальный буфер: текст без пунктуации отдаём частями по ~30 символов.
MAX_DELTA_CHARS = 30


class CjkStreamBuffer:
    """Буфер SSE-текста, не пропускающий иероглифы в `delta` (KI-060).

    Текст копится и отдаётся клиенту только безопасными кусками: до конца
    предложения либо до ~30 символов по границе слова. Если в куске есть
    CJK-иероглифы, предложения с ними вырезаются
    (:func:`app.commentary.semantic.strip_cjk_sentences`); если после
    вырезания не осталось текста, кусок отбрасывается целиком. Иероглифы
    **никогда** не попадают в возвращаемый текст — только в счётчики.

    Args:
        min_chars: минимальный размер куска при резке по пробелу.
        max_chars: максимальный размер буфера до принудительной резки.

    Raises:
        ValueError: параметры вне допустимого диапазона.
    """

    def __init__(
        self,
        *,
        min_chars: int = MIN_DELTA_CHARS,
        max_chars: int = MAX_DELTA_CHARS,
    ) -> None:
        if min_chars < 1:
            raise ValueError("min_chars должен быть ≥ 1")
        if max_chars < min_chars:
            raise ValueError("max_chars должен быть ≥ min_chars")
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._buffer = ""
        self._stripped_cjk_chars = 0
        self._stripped_units = 0

    @property
    def pending(self) -> str:
        """Текст, ещё не отданный клиенту (для диагностики и тестов)."""
        return self._buffer

    @property
    def stripped_cjk_chars(self) -> int:
        """Сколько иероглифов вырезано/отброшено за весь стрим."""
        return self._stripped_cjk_chars

    @property
    def stripped_units(self) -> int:
        """Сколько кусков пришлось чистить от CJK."""
        return self._stripped_units

    def feed(self, chunk: str) -> list[str]:
        """Добавить чанк LLM и вернуть безопасные куски для `delta`."""
        if not chunk:
            return []
        self._buffer += chunk
        pieces: list[str] = []
        while True:
            unit = self._take_unit()
            if unit is None:
                break
            pieces.extend(self._clean_unit(unit))
        return pieces

    def flush(self) -> list[str]:
        """Отдать остаток буфера в конце стрима."""
        unit, self._buffer = self._buffer, ""
        return self._clean_unit(unit)

    def _take_unit(self) -> str | None:
        """Вырезать из буфера следующий кусок, если он уже «созрел»."""
        if not self._buffer:
            return None
        match = _SENTENCE_END.search(self._buffer)
        if match is not None:
            end = match.end()
            # Пробелы после терминатора — часть куска: так очистка
            # предложения с CJK не склеивает соседние слова.
            while end < len(self._buffer) and self._buffer[end].isspace():
                end += 1
            unit = self._buffer[:end]
            self._buffer = self._buffer[end:]
            return unit
        if len(self._buffer) < self._min_chars:
            return None
        limit = min(len(self._buffer), self._max_chars)
        cut = self._buffer.rfind(" ", 0, limit + 1)
        if cut >= self._min_chars - 1:
            unit = self._buffer[: cut + 1]
            self._buffer = self._buffer[cut + 1 :]
            return unit
        if len(self._buffer) >= self._max_chars:
            unit = self._buffer[: self._max_chars]
            self._buffer = self._buffer[self._max_chars :]
            return unit
        return None

    def _clean_unit(self, unit: str) -> list[str]:
        """Очистить кусок от CJK; вернуть `[]`, если текста не осталось."""
        if not unit:
            return []
        if not has_cjk(unit):
            return [unit]
        stripped, _removed = strip_cjk_sentences(unit)
        self._stripped_cjk_chars += count_cjk(unit)
        self._stripped_units += 1
        if not stripped.strip():
            return []
        # Сохраняем границы слов, если вырезание «съело» пробел.
        if unit[:1].isspace() and not stripped[:1].isspace():
            stripped = " " + stripped
        if unit[-1:].isspace() and not stripped[-1:].isspace():
            stripped += " "
        return [stripped]


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
