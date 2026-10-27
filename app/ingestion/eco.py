"""Определение ECO-кода партии (T-007).

`python-chess` **не поставляет базу ECO**: он умеет читать заголовок
``[ECO "..."]`` из PGN (если он там есть), но самостоятельно дебют не
классифицирует. Поэтому работает двухступенчатая схема:

1. **Заголовок PGN** — если ``[ECO]`` присутствует и валиден
   (``A00``…``E99``), он и является ответом. Для партий из баз (TWIC,
   Lichess, ChessBase) это норма.
2. **Курируемая таблица префиксов** — для PGN без заголовка. Ключ — первые
   полуходы в SAN, значение — код ECO; выбирается **самое длинное**
   совпадение. Таблица намеренно небольшая и содержит только те строки,
   в которых автор уверен; она покрывает популярные дебюты, а не весь
   ECO-каталог.

Полная база ECO (≈500 кодов, десятки тысяч линий) — отдельная задача:
её источник — полиглот-книга (`chess.polyglot`) или офлайн-датасет, см.
KI-011. До этого момента ``eco_code = None`` — это честный ответ
«не знаю», а не догадка по первому ходу.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

__all__ = ["OPENING_TABLE", "detect_eco", "lookup_eco", "normalize_eco"]

_ECO_PATTERN: Final = re.compile(r"^[A-E]\d{2}$")

#: Курируемая таблица: префикс SAN → код ECO.
#: Порядок не важен — выбирается самое длинное совпадение.
OPENING_TABLE: Final[dict[tuple[str, ...], str]] = {
    # --- 1.e4 ---
    ("e4",): "B00",
    ("e4", "e5"): "C20",
    ("e4", "e5", "Nf3"): "C40",
    ("e4", "e5", "Nf3", "Nc6"): "C44",
    ("e4", "e5", "Nf3", "Nc6", "Bb5"): "C60",  # Ruy Lopez
    ("e4", "e5", "Nf3", "Nc6", "Bc4"): "C50",  # Italian
    ("e4", "e5", "Nf3", "Nc6", "d4"): "C44",  # Scotch
    ("e4", "e5", "Nf3", "Nf6"): "C42",  # Petrov
    ("e4", "e5", "Nf3", "d6"): "C41",  # Philidor
    ("e4", "e5", "Nf3", "f5"): "C39",  # King's Gambit, Kieseritzky
    ("e4", "e5", "Nc3"): "C25",  # Vienna
    ("e4", "e5", "Bc4"): "C23",  # Bishop's opening
    ("e4", "e5", "f4"): "C30",  # King's Gambit
    ("e4", "c5"): "B20",  # Sicilian
    ("e4", "c5", "Nf3"): "B27",  # Sicilian, early
    ("e4", "c5", "Nf3", "d6"): "B50",
    ("e4", "c5", "Nf3", "Nc6"): "B30",
    ("e4", "c5", "Nc3"): "B23",  # Closed Sicilian
    ("e4", "c5", "c3"): "B22",  # Alapin
    ("e4", "e6"): "C00",  # French
    ("e4", "e6", "d4", "d5"): "C01",  # French, Exchange
    ("e4", "e6", "d4", "d5", "Nc3"): "C10",  # French, Paulsen
    ("e4", "c6"): "B10",  # Caro-Kann
    ("e4", "c6", "d4", "d5"): "B12",
    ("e4", "c6", "d4", "d5", "Nc3"): "B15",
    ("e4", "d5"): "B01",  # Scandinavian
    ("e4", "Nf6"): "B02",  # Alekhine
    ("e4", "d6"): "B07",  # Pirc
    ("e4", "g6"): "B06",  # Modern
    ("e4", "Nc6"): "B00",  # Nimzowitsch
    # --- 1.d4 ---
    ("d4",): "A40",
    ("d4", "d5"): "D00",
    ("d4", "d5", "c4"): "D06",  # Queen's Gambit
    ("d4", "d5", "c4", "e6"): "D30",  # QGD
    ("d4", "d5", "c4", "c6"): "D10",  # Slav
    ("d4", "d5", "c4", "dxc4"): "D20",  # QGA
    ("d4", "Nf6"): "A45",  # Indian
    ("d4", "Nf6", "c4"): "A50",
    ("d4", "Nf6", "c4", "e6"): "E00",
    ("d4", "Nf6", "c4", "e6", "Nc3", "Bb4"): "E20",  # Nimzo-Indian
    ("d4", "Nf6", "c4", "e6", "Nc3", "Bb4", "Nf3"): "E21",  # Nimzo, 4.Nf3
    ("d4", "Nf6", "c4", "g6"): "E60",  # King's Indian
    ("d4", "Nf6", "c4", "g6", "Nc3", "Bg7"): "E60",
    ("d4", "Nf6", "c4", "g6", "Nc3", "d5"): "D80",  # Grünfeld
    ("d4", "f5"): "A80",  # Dutch
    # --- фланговые ---
    ("c4",): "A10",  # English
    ("c4", "e5"): "A20",
    ("c4", "c5"): "A30",
    ("c4", "Nf6"): "A15",
    ("Nf3",): "A04",  # Réti
    ("g3",): "A00",  # Benko / Hungarian
    ("b3",): "A01",  # Larsen
    ("f4",): "A02",  # Bird
    ("b4",): "A00",  # Polish
}


def normalize_eco(code: str | None) -> str | None:
    """Привести код ECO к канону (``e21`` → ``E21``) или вернуть ``None``."""
    if code is None:
        return None
    candidate = code.strip().upper()
    return candidate if _ECO_PATTERN.match(candidate) else None


def lookup_eco(san_moves: Sequence[str]) -> str | None:
    """Определить ECO по таблице префиксов (самое длинное совпадение)."""
    best: tuple[int, str] | None = None
    for prefix, code in OPENING_TABLE.items():
        size = len(prefix)
        if size > len(san_moves):
            continue
        if tuple(san_moves[:size]) != prefix:
            continue
        if best is None or size > best[0]:
            best = (size, code)
    return None if best is None else best[1]


def detect_eco(eco_header: str | None, san_moves: Sequence[str]) -> str | None:
    """ECO из заголовка PGN, иначе — из таблицы префиксов, иначе ``None``."""
    return normalize_eco(eco_header) or lookup_eco(san_moves)
