"""Диаграммы доски для PDF-экспорта комментария (T-107b).

Зачем отдельный модуль:

* **SVG — источник, PNG — вставка.** `python-chess` умеет рисовать доску в
  SVG (`chess.svg.board`), а `fpdf2` вставляет растровые изображения.
  Конвертация SVG → PNG выполняется `cairosvg` (нативный cairo).
* **Graceful degradation.** `cairosvg` — Python-пакет, но ему нужна
  системная библиотека cairo (`libcairo2` в Docker/CI). На машине без неё
  импорт падает с `OSError`, а не `ImportError`. Поэтому загрузка модуля и
  проверка работоспособности — ленивые: при отсутствии рендерера
  `render_board_png` возвращает ``None``, а PDF собирается без картинки
  (T-107b, DoD: «не падать с 500»).
* **Никакой связи с БД.** На вход — FEN и метаданные хода; вызывающий
  (эндпоинт PDF) сам решает, какие позиции показывать.

Выбор позиций (`select_diagrams`): финальная позиция партии + до двух
критических моментов (зевки/ошибки с наибольшим `delta_cp`). Дубликат
финальной позиции не добавляется.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal

import chess
import chess.svg
import structlog

__all__ = [
    "BoardDiagram",
    "BoardOrientation",
    "MoveSnapshot",
    "board_svg",
    "cairosvg_available",
    "render_board_png",
    "select_diagrams",
]

logger = structlog.get_logger(__name__)

#: Сторона, с которой рисуется доска.
BoardOrientation = Literal["white", "black"]

#: Размер PNG по умолчанию (px). 640 px на ~80 мм — ~200 DPI, печать чёткая.
BOARD_PX: Final[int] = 640

#: Классификации, которые считаются критическими моментами.
_CRITICAL_CLASSES: Final[frozenset[str]] = frozenset({"blunder", "mistake"})

#: Подписи классификаций для caption-а диаграммы.
_CLASS_LABELS: Final[dict[str, str]] = {
    "blunder": "зевок",
    "mistake": "ошибка",
    "inaccuracy": "неточность",
}

#: Минимальный SVG для проверки, что cairo реально работает (а не только
#: импортируется): на Windows `import cairosvg` падает, а в Linux без
#: libcairo2 — тоже; проверка кэшируется на процесс.
_PROBE_SVG: Final[str] = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    '<rect width="8" height="8" fill="white"/></svg>'
)


@dataclass(frozen=True, slots=True)
class MoveSnapshot:
    """Минимальные данные хода для выбора диаграмм (без ORM)."""

    ply: int
    san: str
    fen: str
    classification: str | None = None
    delta_cp: int | None = None


@dataclass(frozen=True, slots=True)
class BoardDiagram:
    """Диаграмма: позиция, подпись и подсветка последнего хода."""

    fen: str
    caption: str = ""
    last_move_uci: str | None = None
    orientation: BoardOrientation = "white"


def board_svg(
    diagram: BoardDiagram, *, size: int = BOARD_PX
) -> str:
    """Отрисовать позицию в SVG через `chess.svg.board`.

    Raises:
        ValueError: FEN/ход не разобраны python-chess.
    """
    board = chess.Board(diagram.fen)
    last_move: chess.Move | None = None
    if diagram.last_move_uci:
        try:
            last_move = chess.Move.from_uci(diagram.last_move_uci)
        except ValueError:
            # Битый/несуществующий UCI — не повод не показывать позицию.
            last_move = None
    svg: str = chess.svg.board(
        board=board,
        lastmove=last_move,
        orientation=(
            chess.WHITE if diagram.orientation == "white" else chess.BLACK
        ),
        size=size,
        coordinates=True,
    )
    return svg


@lru_cache(maxsize=1)
def cairosvg_available() -> bool:
    """Работает ли `cairosvg` в этом окружении (импорт + пробный рендер).

    Проверка нужна, потому что на Windows без Cairo DLL импорт падает с
    ``OSError``, а не ``ImportError``. Результат кэшируется: каждая
    проверка — это реальный рендер, повторять его на каждый PDF незачем.
    """
    try:
        import cairosvg

        cairosvg.svg2png(
            bytestring=_PROBE_SVG.encode("utf-8"),
            output_width=8,
            output_height=8,
        )
    except Exception as exc:
        logger.warning(
            "board_renderer_unavailable",
            reason=type(exc).__name__,
            error=str(exc).splitlines()[0] if str(exc) else "",
        )
        return False
    return True


def render_board_png(
    diagram: BoardDiagram, *, size: int = BOARD_PX
) -> bytes | None:
    """SVG доски → PNG (``None``, если рендерер недоступен/сломан).

    Ошибки рендера не пробрасываются: PDF-экспорт — пользовательская
    операция, и отсутствие системной библиотеки не должно давать 500.
    """
    if not cairosvg_available():
        return None
    try:
        svg = board_svg(diagram, size=size)
        import cairosvg

        png = cairosvg.svg2png(
            bytestring=svg.encode("utf-8"),
            output_width=size,
            output_height=size,
            background_color="white",
        )
    except Exception as exc:
        logger.warning(
            "board_render_failed",
            reason=type(exc).__name__,
            error=str(exc).splitlines()[0] if str(exc) else "",
        )
        return None
    if not isinstance(png, bytes):  # pragma: no cover - контракт cairosvg
        return None
    return png


def _move_ucis(
    initial_fen: str, moves: tuple[MoveSnapshot, ...]
) -> dict[int, str]:
    """UCI каждого полухода: проигрываем SAN от начальной позиции.

    В `moves` хранится SAN и FEN после хода, но не UCI. Для подсветки
    последнего хода на диаграмме нужен именно UCI, поэтому партия один раз
    «проигрывается» python-chess. При любой ошибке разбора возвращаем то,
    что успели восстановить: диаграмма без подсветки лучше, чем 500.
    """
    try:
        board = chess.Board(initial_fen)
    except ValueError:
        return {}
    result: dict[int, str] = {}
    for move in moves:
        try:
            parsed = board.parse_san(move.san)
        except ValueError:
            break
        result[move.ply] = parsed.uci()
        board.push(parsed)
    return result


def _move_number(ply: int) -> str:
    """Нотация номера хода: белый — ``12.``, чёрный — ``12...``."""
    number = (ply + 1) // 2
    return f"{number}." if ply % 2 == 1 else f"{number}..."


def _safe_diagram(
    move: MoveSnapshot,
    *,
    caption: str,
    ucis: dict[int, str],
    orientation: BoardOrientation = "white",
) -> BoardDiagram | None:
    """Собрать диаграмму, проверив FEN (битый FEN — пропуск, не ошибка)."""
    try:
        chess.Board(move.fen)
    except ValueError:
        logger.warning("board_diagram_invalid_fen", ply=move.ply)
        return None
    return BoardDiagram(
        fen=move.fen,
        caption=caption,
        last_move_uci=ucis.get(move.ply),
        orientation=orientation,
    )


def select_diagrams(
    moves: tuple[MoveSnapshot, ...],
    *,
    initial_fen: str,
    max_critical: int = 2,
) -> tuple[BoardDiagram, ...]:
    """Выбрать диаграммы для PDF: финальная позиция + критические моменты.

    Args:
        moves: полуходы партии в порядке игры (SAN/FEN/классификация).
        initial_fen: FEN до первого хода (для восстановления UCI).
        max_critical: максимум диаграмм критических моментов (0 — только
            финальная позиция).

    Returns:
        Кортеж диаграмм; пустой, если ходов нет.
    """
    if not moves:
        return ()
    ucis = _move_ucis(initial_fen, moves)
    final_move = moves[-1]
    final = _safe_diagram(
        final_move,
        caption=f"Финальная позиция (после {len(moves)} полуходов)",
        ucis=ucis,
    )
    critical: list[BoardDiagram] = []
    if max_critical > 0:
        candidates = [
            move
            for move in moves
            if move.classification in _CRITICAL_CLASSES
            and move.ply != final_move.ply
        ]
        # Сначала самые дорогие ошибки; при равном delta_cp — поздние.
        candidates.sort(
            key=lambda move: (abs(move.delta_cp or 0), move.ply),
            reverse=True,
        )
        for move in candidates[:max_critical]:
            label = _CLASS_LABELS.get(move.classification or "", "ошибка")
            diagram = _safe_diagram(
                move,
                caption=(
                    f"Критический момент: {_move_number(move.ply)} "
                    f"{move.san} — {label}"
                ),
                ucis=ucis,
            )
            if diagram is not None:
                critical.append(diagram)
    result = [final] if final is not None else []
    result.extend(critical)
    return tuple(result)
