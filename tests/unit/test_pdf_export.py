"""Unit-тесты генератора PDF комментария (T-107) без HTTP/БД.

Проверяем: поиск шрифта, замену неподдерживаемых символов, нотацию ходов
и то, что `build_commentary_pdf` отдаёт валидный PDF, из которого pypdf
извлекает кириллический текст. Если в окружении нет TTF-шрифта с
кириллицей, тесты генерации скипаются (см. `PDF_FONT_PATH`).
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from app.config import get_settings
from app.errors import PdfFontUnavailableError
from app.export.pdf import (
    CommentaryPdfData,
    _sanitize,
    _supported_codepoints,
    build_commentary_pdf,
    find_pdf_font,
    format_moves,
)


def _font_available() -> bool:
    try:
        find_pdf_font(get_settings())
    except PdfFontUnavailableError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _font_available(), reason="нет TTF-шрифта для PDF"
)


def _sample() -> CommentaryPdfData:
    return CommentaryPdfData(
        commentary_id=42,
        white="Иван Петров",
        black="Пётр Сидоров",
        event="Клубный турнир",
        date="2026.10.29",
        result="1/2-1/2",
        level="club",
        model="qwen/qwen3-4b-2507",
        moves=("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"),
        commentary_text=(
            "Первый абзац комментария с кириллицей.\n\n"
            "Второй абзац: после 1. e4 e5 позиция симметрична."
        ),
        validated=True,
        human_edited=True,
    )


def _pdf_text(raw: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw))
    return "".join(page.extract_text() or "" for page in reader.pages)


def test_find_pdf_font_returns_existing_file() -> None:
    path = find_pdf_font(get_settings())
    assert path.is_file()


def test_find_pdf_font_explicit_missing_path_raises() -> None:
    settings = get_settings().model_copy(
        update={"pdf_font_path": "C:/definitely/missing/font.ttf"}
    )
    with pytest.raises(PdfFontUnavailableError, match="несуществующий"):
        find_pdf_font(settings)


def test_format_moves_numbers_full_moves() -> None:
    assert format_moves(("e4", "e5", "Nf3")) == "1. e4 e5 2. Nf3"
    assert format_moves(()) == ""


def test_sanitize_replaces_unsupported_glyphs() -> None:
    font = find_pdf_font(get_settings())
    supported = _supported_codepoints(str(font))
    # U+1FAE0 (эмодзи) почти наверняка отсутствует в Arial/DejaVuSans.
    sanitized = _sanitize("привет \U0001fae0 мир\nстрока", supported)
    assert "\U0001fae0" not in sanitized
    assert "?" in sanitized
    assert "привет" in sanitized
    assert "\n" in sanitized


def test_build_commentary_pdf_is_valid_and_contains_text() -> None:
    raw = build_commentary_pdf(_sample())
    assert raw.startswith(b"%PDF-")
    assert raw.rstrip().endswith(b"%%EOF")

    text = _pdf_text(raw)
    for expected in (
        "Иван Петров",
        "Пётр Сидоров",
        "Клубный турнир",
        "1. e4 e5 2. Nf3 Nc6",
        "Первый абзац комментария",
        "Второй абзац",
        "клубный игрок",
        "разбор партии",
    ):
        assert expected.lower() in text.lower(), expected


def test_build_commentary_pdf_empty_commentary() -> None:
    data = CommentaryPdfData(
        commentary_id=1,
        white="A",
        black="B",
        moves=(),
        commentary_text="",
    )
    raw = build_commentary_pdf(data)
    assert raw.startswith(b"%PDF-")
    assert "Комментарий" in _pdf_text(raw)
