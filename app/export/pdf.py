"""PDF-экспорт комментария к партии (T-107, итерация 30).

Почему `fpdf2`, а не `weasyprint`:

* `weasyprint` требует нативных GTK/Pango/cairo — на Windows-машине
  разработчика и в slim-Docker/CI это дополнительная системная установка;
  проект собирает wheel без системных библиотек.
* `fpdf2` — чистый Python, поддерживает Unicode через TrueType-шрифт:
  кириллица в шапке, ходах и комментарии рендерится корректно.

Шрифт ищется в системных каталогах (Arial на Windows, DejaVuSans на
Linux, Arial на macOS) или задаётся `PDF_FONT_PATH`; найденный TTF
встраивается в PDF подмножеством (fpdf2/fontTools). Символы, которых нет
в шрифте (эмодзи и т.п.), заменяются на «?», чтобы произвольный текст
пользовательской правки не ронял экспорт.

Документ: шапка (игроки, турнир, дата, результат, уровень, модель,
признак правки), PGN-ходы в нумерации, диаграммы доски (T-107b:
финальная позиция и критические моменты, если доступен `cairosvg`) и
текст комментария с сохранением абзацев; внизу — номер страницы.
Генерация CPU-bound, поэтому эндпоинт вызывает её через
`asyncio.to_thread` (RULES §3.1).

Диаграммы — опциональны: при отсутствии `cairosvg`/`libcairo2`
:func:`app.export.board.render_board_png` возвращает ``None``, и PDF
собирается без картинок (graceful degradation, не 500).
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Final

import structlog
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
from fpdf import FPDF
from fpdf.enums import WrapMode, XPos, YPos

from app.config import Settings, get_settings
from app.errors import PdfFontUnavailableError
from app.export.board import BoardDiagram, render_board_png

__all__ = [
    "CommentaryPdfData",
    "build_commentary_pdf",
    "find_pdf_font",
    "format_moves",
]

logger = structlog.get_logger(__name__)

#: Поля страницы и базовые отступы (мм).
_MARGIN_MM: Final[float] = 18.0

#: Ширина диаграммы доски в PDF (мм); сама доска квадратная.
_BOARD_WIDTH_MM: Final[float] = 82.0

#: Уровни ассистента → человекочитаемые подписи в PDF.
_LEVEL_LABELS: Final[dict[str, str]] = {
    "beginner": "новичок",
    "club": "клубный игрок",
    "gm": "гроссмейстер",
}

#: Кандидаты TTF-шрифтов с кириллицей (Windows → Linux → macOS).
_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)


@dataclass(frozen=True, slots=True)
class CommentaryPdfData:
    """Данные для PDF: всё уже прочитано из БД/партии."""

    commentary_id: int
    white: str | None = None
    black: str | None = None
    event: str | None = None
    date: str | None = None
    result: str | None = None
    level: str = "club"
    model: str = ""
    moves: tuple[str, ...] = ()
    commentary_text: str = ""
    #: Диаграммы доски (T-107b); пусто — PDF без картинок.
    diagrams: tuple[BoardDiagram, ...] = ()
    validated: bool = False
    human_edited: bool = False
    generated_at: datetime | None = None


def find_pdf_font(settings: Settings | None = None) -> Path:
    """Найти TTF-шрифт с кириллицей.

    Если `PDF_FONT_PATH` задан явно, он обязателен: тихий fallback на
    системный шрифт скрыл бы ошибку конфигурации.

    Raises:
        PdfFontUnavailableError: ни один кандидат не найден.
    """
    cfg = settings or get_settings()
    explicit = (cfg.pdf_font_path or "").strip()
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise PdfFontUnavailableError(
            f"PDF_FONT_PATH указывает на несуществующий файл: {explicit}"
        )
    for raw in _FONT_CANDIDATES:
        path = Path(raw)
        if path.is_file():
            return path
    raise PdfFontUnavailableError(
        "Не найден TTF-шрифт с кириллицей для PDF; задайте PDF_FONT_PATH "
        "(например, C:/Windows/Fonts/arial.ttf или "
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf)"
    )


@lru_cache(maxsize=4)
def _supported_codepoints(font_path: str) -> frozenset[int]:
    """Коды символов, которые есть в cmap шрифта (для замены «?»)."""
    font = TTFont(font_path, fontNumber=0, lazy=True)
    try:
        cmap = font.getBestCmap() or {}
        return frozenset(int(code) for code in cmap)
    finally:
        font.close()


def _sanitize(text: str, supported: frozenset[int]) -> str:
    """Оставить только символы шрифта; переводы строк сохранить."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\t", "    ")
    chars: list[str] = []
    for char in normalized:
        if char == "\n" or ord(char) in supported:
            chars.append(char)
        else:
            chars.append("?")
    return "".join(chars)


def format_moves(moves: tuple[str, ...]) -> str:
    """SAN-ходы → нотация с номерами: ``1. e4 e5 2. Nf3 Nc6``."""
    parts: list[str] = []
    for index, san in enumerate(moves):
        if index % 2 == 0:
            parts.append(f"{index // 2 + 1}. {san}")
        else:
            parts.append(san)
    return " ".join(parts)


class _CommentaryPdf(FPDF):
    """PDF с нижним колонтитулом (подпись + ``N/{nb}``)."""

    def __init__(self, *, footer_label: str) -> None:
        super().__init__(format="A4")
        self._footer_label = footer_label

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Body", size=8)
        self.set_text_color(130, 130, 130)
        self.cell(
            0,
            6,
            f"ChessAnnotator · {self._footer_label} · стр. "
            f"{self.page_no()}/{{nb}}",
            align="C",
        )


def _paragraphs(text: str) -> list[str]:
    """Разбить текст на абзацы по пустым строкам (сохраняя смысл)."""
    chunks = re.split(r"\n\s*\n", text.strip())
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _write(
    pdf: FPDF,
    text: str,
    *,
    height: float = 6.0,
    wrapmode: WrapMode = WrapMode.CHAR,
) -> None:
    """Записать блок текста с переносом и возвратом курсора к левому краю.

    В fpdf2 2.8 `multi_cell` по умолчанию оставляет курсор справа; без
    явных `new_x`/`new_y` следующий блок получает нулевую ширину (и
    `wrapmode="CHAR"` может зациклиться на подборе символа).
    """
    pdf.multi_cell(
        0,
        height,
        text,
        wrapmode=wrapmode,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )


def _draw_diagrams(
    pdf: FPDF,
    diagrams: tuple[BoardDiagram, ...],
    text: Callable[[str], str],
) -> int:
    """Нарисовать диаграммы доски; вернуть число вставленных картинок.

    PNG рендерится заранее: если рендерер недоступен, заголовок раздела не
    печатается вовсе (иначе в PDF остался бы пустой блок). Ошибка вставки
    одного изображения не отменяет остальные и не роняет генерацию.
    """
    rendered: list[tuple[BoardDiagram, bytes]] = []
    for diagram in diagrams:
        png = render_board_png(diagram)
        if png is None:
            logger.info(
                "pdf_board_diagram_skipped", caption=diagram.caption
            )
            continue
        rendered.append((diagram, png))
    if not rendered:
        return 0

    pdf.set_text_color(25, 25, 25)
    pdf.set_font("Body", size=13)
    _write(pdf, text("Позиции на доске"), height=7)
    inserted = 0
    for diagram, png in rendered:
        # Диаграмма квадратная: 82 мм ширины + подпись ~7 мм.
        if pdf.get_y() + _BOARD_WIDTH_MM + 14 > pdf.h - pdf.b_margin:
            pdf.add_page()
        top = pdf.get_y()
        x = (pdf.w - _BOARD_WIDTH_MM) / 2
        try:
            pdf.image(io.BytesIO(png), x=x, y=top, w=_BOARD_WIDTH_MM)
        except Exception as exc:
            logger.warning(
                "pdf_board_image_failed",
                reason=type(exc).__name__,
                caption=diagram.caption,
            )
            pdf.set_y(top + 4)
            continue
        inserted += 1
        pdf.set_y(top + _BOARD_WIDTH_MM + 2)
        if diagram.caption:
            pdf.set_font("Body", size=9)
            pdf.set_text_color(90, 90, 90)
            pdf.cell(
                0,
                5,
                text(diagram.caption),
                align="C",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        pdf.ln(3)
    pdf.set_text_color(25, 25, 25)
    return inserted


def build_commentary_pdf(
    data: CommentaryPdfData, settings: Settings | None = None
) -> bytes:
    """Сформировать PDF-байты комментария.

    Raises:
        PdfFontUnavailableError: нет шрифта с кириллицей.
    """
    cfg = settings or get_settings()
    font_path = find_pdf_font(cfg)
    supported = _supported_codepoints(str(font_path))

    def text(value: str) -> str:
        return _sanitize(value, supported)

    pdf = _CommentaryPdf(
        footer_label=text(f"Комментарий #{data.commentary_id}")
    )
    pdf.set_margins(_MARGIN_MM, _MARGIN_MM, _MARGIN_MM)
    pdf.set_auto_page_break(auto=True, margin=_MARGIN_MM)
    pdf.add_font("Body", "", str(font_path))
    pdf.set_font("Body", size=11)
    pdf.alias_nb_pages()
    pdf.set_title(text("ChessAnnotator — разбор партии"))
    pdf.set_creator("ChessAnnotator")
    pdf.add_page()

    # Заголовок.
    pdf.set_text_color(25, 25, 25)
    pdf.set_font("Body", size=20)
    _write(pdf, text("Разбор партии"), height=10)
    pdf.set_font("Body", size=9)
    pdf.set_text_color(110, 110, 110)
    _write(
        pdf,
        text(
            "ChessAnnotator · комментарий, сгенерированный по анализу Stockfish"
        ),
        height=5,
    )
    pdf.ln(2)
    pdf.set_draw_color(205, 205, 205)
    pdf.line(
        pdf.l_margin,
        pdf.get_y(),
        pdf.w - pdf.r_margin,
        pdf.get_y(),
    )
    pdf.ln(4)

    # Шапка: игроки и метаданные.
    white = data.white or "—"
    black = data.black or "—"
    pdf.set_font("Body", size=13)
    pdf.set_text_color(25, 25, 25)
    _write(pdf, text(f"{white} — {black}"), height=8)

    meta: list[str] = []
    if data.event:
        meta.append(f"Турнир: {data.event}")
    if data.date:
        meta.append(f"Дата: {data.date}")
    if data.result:
        meta.append(f"Результат: {data.result}")
    meta.append(
        f"Уровень: {_LEVEL_LABELS.get(data.level, data.level)}"
    )
    if data.model:
        meta.append(f"Модель: {data.model}")
    status: list[str] = [
        "валидирован" if data.validated else "без валидации"
    ]
    if data.human_edited:
        status.append("есть правка человека")
    meta.append("Статус: " + ", ".join(status))
    pdf.set_font("Body", size=10)
    pdf.set_text_color(85, 85, 85)
    _write(pdf, text("\n".join(meta)), height=5)
    pdf.ln(4)

    # Ходы партии.
    pdf.set_text_color(25, 25, 25)
    pdf.set_font("Body", size=13)
    _write(pdf, text("Ходы"), height=7)
    pdf.set_font("Body", size=10)
    moves_text = format_moves(data.moves) or "—"
    _write(pdf, text(moves_text), height=5)
    pdf.ln(4)

    # Диаграммы доски (T-107b): финальная позиция и критические моменты.
    _draw_diagrams(pdf, data.diagrams, text)
    pdf.set_text_color(25, 25, 25)

    # Комментарий.
    pdf.set_font("Body", size=13)
    _write(pdf, text("Комментарий"), height=7)
    pdf.set_font("Body", size=11)
    paragraphs = _paragraphs(data.commentary_text)
    if not paragraphs:
        _write(pdf, text("—"), height=6)
    for paragraph in paragraphs:
        _write(pdf, text(paragraph), height=6)
        pdf.ln(2)

    # Подпись генерации (UTC, стабильно независимо от TZ пользователя).
    generated = data.generated_at or datetime.now(UTC)
    pdf.set_font("Body", size=8)
    pdf.set_text_color(130, 130, 130)
    pdf.ln(2)
    _write(
        pdf,
        text(
            "Сгенерировано ChessAnnotator "
            f"{generated.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
        ),
        height=4,
    )

    return bytes(pdf.output())
