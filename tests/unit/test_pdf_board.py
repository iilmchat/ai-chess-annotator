"""Диаграммы доски в PDF (T-107b).

Проверяем три уровня:

* чистую генерацию SVG/выбор позиций (`chess.svg` + `select_diagrams`);
* деградацию без `cairosvg`/libcairo2 — `render_board_png` возвращает
  ``None``, а PDF собирается без картинки и без исключения;
* вставку PNG в PDF (fpdf2) — через pypdf: страница содержит image-XObject
  и подпись раздела. Реальный рендер cairosvg проверяется отдельным тестом
  и скипается, если системной библиотеки cairo нет (Windows-dev).
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from pypdf import PdfReader

from app.config import get_settings
from app.errors import PdfFontUnavailableError
from app.export.board import (
    BoardDiagram,
    MoveSnapshot,
    board_svg,
    cairosvg_available,
    render_board_png,
    select_diagrams,
)
from app.export.pdf import (
    CommentaryPdfData,
    build_commentary_pdf,
    find_pdf_font,
)
from app.ingestion import STARTING_FEN, parse_pgn

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"


def _font_available() -> bool:
    try:
        find_pdf_font(get_settings())
    except PdfFontUnavailableError:
        return False
    return True


def _snapshots() -> tuple[MoveSnapshot, ...]:
    parsed = parse_pgn(PGN)
    return tuple(
        MoveSnapshot(
            ply=move.ply,
            san=move.san,
            fen=move.fen,
            classification=(
                "mistake"
                if move.ply == 2
                else "blunder"
                if move.ply == 4
                else "good"
            ),
            delta_cp=-120 if move.ply == 2 else -450 if move.ply == 4 else 0,
        )
        for move in parsed.moves
    )


def _initial_fen() -> str:
    return parse_pgn(PGN).initial_fen


def _png_bytes() -> bytes:
    """Крошечный валидный PNG (без cairo) — для проверки вставки в PDF."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), (245, 245, 245)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_board_svg_draws_start_position() -> None:
    svg = board_svg(BoardDiagram(fen=STARTING_FEN), size=240)
    assert svg.startswith("<svg")
    assert "white-pawn" in svg
    assert "black-king" in svg


def test_board_svg_ignores_broken_last_move() -> None:
    """Битый UCI не мешает нарисовать позицию (подсветка просто пропадает)."""
    svg = board_svg(
        BoardDiagram(fen=STARTING_FEN, last_move_uci="not-a-move"), size=160
    )
    assert svg.startswith("<svg")


def test_select_diagrams_adds_final_and_critical_positions() -> None:
    diagrams = select_diagrams(_snapshots(), initial_fen=_initial_fen())
    assert len(diagrams) == 3
    assert diagrams[0].caption == "Финальная позиция (после 6 полуходов)"
    # Критические — по убыванию |delta_cp|: blunder (ply 4) раньше mistake.
    assert diagrams[1].caption == "Критический момент: 2... Nc6 — зевок"
    assert diagrams[2].caption == "Критический момент: 1... e5 — ошибка"
    assert diagrams[0].last_move_uci == "f8c5"
    assert diagrams[1].fen == _snapshots()[3].fen


def test_select_diagrams_respects_max_critical() -> None:
    diagrams = select_diagrams(
        _snapshots(), initial_fen=_initial_fen(), max_critical=0
    )
    assert len(diagrams) == 1
    assert diagrams[0].caption.startswith("Финальная позиция")


def test_select_diagrams_without_moves_is_empty() -> None:
    assert select_diagrams((), initial_fen=STARTING_FEN) == ()


def test_render_board_png_degrades_without_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.export.board.cairosvg_available", lambda: False
    )
    assert render_board_png(BoardDiagram(fen=STARTING_FEN)) is None


@pytest.mark.skipif(
    not cairosvg_available(), reason="cairosvg/libcairo2 недоступны"
)
def test_render_board_png_produces_png() -> None:
    png = render_board_png(BoardDiagram(fen=STARTING_FEN), size=200)
    assert png is not None
    assert png.startswith(b"\x89PNG")


pytest_pdf = pytest.mark.skipif(
    not _font_available(), reason="нет TTF-шрифта для PDF"
)


@pytest_pdf
def test_pdf_embeds_board_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """fpdf2 вставляет PNG; pypdf видит картинку и подпись раздела."""
    monkeypatch.setattr(
        "app.export.pdf.render_board_png",
        lambda diagram: _png_bytes(),
    )
    data = CommentaryPdfData(
        commentary_id=1,
        white="A",
        black="B",
        moves=("e4", "e5"),
        commentary_text="Комментарий.",
        diagrams=(
            BoardDiagram(fen=STARTING_FEN, caption="Финальная позиция"),
        ),
    )
    raw = build_commentary_pdf(data)
    reader = PdfReader(io.BytesIO(raw))
    assert reader.pages
    text = reader.pages[0].extract_text() or ""
    assert "Позиции на доске" in text
    assert "Финальная позиция" in text
    images: Iterator[object] = iter(reader.pages[0].images)
    assert next(images, None) is not None


@pytest_pdf
def test_pdf_without_renderer_has_no_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graceful degradation: нет рендерера — валидный PDF без диаграмм."""
    monkeypatch.setattr(
        "app.export.pdf.render_board_png", lambda diagram: None
    )
    data = CommentaryPdfData(
        commentary_id=2,
        moves=("e4", "e5"),
        commentary_text="Комментарий.",
        diagrams=(
            BoardDiagram(fen=STARTING_FEN, caption="Финальная позиция"),
        ),
    )
    raw = build_commentary_pdf(data)
    assert raw.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(raw))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    assert "Комментарий" in text
    assert "Позиции на доске" not in text
    assert all(not page.images for page in reader.pages)
