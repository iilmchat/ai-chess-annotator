"""Unit-тесты детектора CJK-иероглифов (KI-060, итерация 49).

Проверяются три слоя:

* обнаружение символов CJK Unified Ideographs (U+4E00–U+9FFF), включая
  граничные кодовые точки;
* вырезание предложений с иероглифами (абзацы сохраняются);
* решение «очистить или отклонить» на последней попытке генерации.
"""

from __future__ import annotations

import pytest

from app.commentary.semantic import (
    CJK_MAX_SHARE,
    CjkSanitizeResult,
    cjk_share,
    cjk_warning,
    count_cjk,
    has_cjk,
    sanitize_cjk,
    strip_cjk_sentences,
)

# --- обнаружение ------------------------------------------------------------


def test_has_cjk_in_mixed_russian_text() -> None:
    """Пример из KI-060: иероглиф внутри шахматной фразы."""
    assert has_cjk("Конь идет на 马e4") is True
    assert count_cjk("Конь идет на 马e4") == 1


def test_has_cjk_clean_russian_text() -> None:
    text = "Партия прошла в напряжённой борьбе. Белые захватили центр."

    assert has_cjk(text) is False
    assert count_cjk(text) == 0


def test_has_cjk_clean_english_text() -> None:
    assert has_cjk("The knight goes to e4 and attacks the centre.") is False


@pytest.mark.parametrize(
    ("char", "expected"),
    [
        ("\u4e00", True),  # первая кодовая точка диапазона
        ("\u9fff", True),  # последняя кодовая точка диапазона
        ("\u4dff", False),  # перед диапазоном (CJK Extension A)
        ("\ua000", False),  # сразу после диапазона (Yi Syllables)
        ("\u3400", False),  # CJK Extension A
        ("\u3042", False),  # хирагана, не иероглиф
    ],
)
def test_cjk_range_boundaries(char: str, expected: bool) -> None:
    assert has_cjk(char) is expected


def test_count_cjk_multiple_and_deduplicated_by_occurrence() -> None:
    assert count_cjk("马马马 这是中文") == 7


def test_cjk_share_of_empty_text_is_zero() -> None:
    assert cjk_share("") == 0.0
    assert cjk_share("   \n\t") == 0.0


def test_cjk_share_ignores_whitespace() -> None:
    # 2 иероглифа на 6 непустых символов (马,马,a,b,c,d) = 1/3.
    assert cjk_share("马马 abcd") == pytest.approx(2 / 6)


# --- вырезание предложений --------------------------------------------------


def test_strip_cjk_removes_only_offending_sentence() -> None:
    text = "Партия была интересной. Конь идет на 马e4. Затем белые выиграли."

    stripped, removed = strip_cjk_sentences(text)

    assert stripped == "Партия была интересной. Затем белые выиграли."
    assert removed == 1


def test_strip_cjk_keeps_paragraphs() -> None:
    text = "Первый абзац.\n\n马马马。\n\nВторой абзац."

    stripped, removed = strip_cjk_sentences(text)

    assert stripped == "Первый абзац.\n\nВторой абзац."
    assert removed == 1


def test_strip_cjk_returns_original_when_clean() -> None:
    text = "Обычный комментарий без иероглифов."

    assert strip_cjk_sentences(text) == (text, 0)


def test_strip_cjk_returns_empty_when_everything_is_cjk() -> None:
    stripped, removed = strip_cjk_sentences("马马马。这是中文。")

    assert stripped == ""
    assert removed == 2


def test_strip_cjk_handles_single_line_without_terminator() -> None:
    assert strip_cjk_sentences("白方优势") == ("", 1)


# --- очистка/отклонение -----------------------------------------------------


def test_sanitize_clean_text_is_noop() -> None:
    result = sanitize_cjk("Комментарий без иероглифов.")

    assert result == CjkSanitizeResult(
        text="Комментарий без иероглифов.",
        cjk_chars=0,
        removed_sentences=0,
        share=0.0,
        rejected=False,
    )


def test_sanitize_strips_single_drifted_sentence() -> None:
    result = sanitize_cjk(
        "Партия была интересной. Конь идет на 马e4."
    )

    assert result.rejected is False
    assert result.text == "Партия была интересной."
    assert result.cjk_chars == 1
    assert result.removed_sentences == 1
    assert result.share < CJK_MAX_SHARE


def test_sanitize_rejects_mostly_cjk_answer() -> None:
    result = sanitize_cjk("马马马 这是中文")

    assert result.rejected is True
    assert result.text == ""
    assert result.share > CJK_MAX_SHARE


def test_sanitize_rejects_when_only_cjk_sentence_remains() -> None:
    """«Конь идет на 马e4» — единственное предложение: вырезать нечего."""
    result = sanitize_cjk("Конь идет на 马e4")

    assert result.rejected is True
    assert result.text == ""
    assert result.removed_sentences == 1


def test_sanitize_respects_custom_threshold() -> None:
    text = "Конь идет на 马e4 и атакует."

    lenient = sanitize_cjk(text, max_share=0.5)
    strict = sanitize_cjk(text, max_share=0.0)

    assert lenient.rejected is True  # единственное предложение вырезано
    assert strict.rejected is True


@pytest.mark.parametrize("max_share", [-0.1, 1.1])
def test_sanitize_rejects_invalid_threshold(max_share: float) -> None:
    with pytest.raises(ValueError, match=r"0\.\.1"):
        sanitize_cjk("текст", max_share=max_share)


# --- подсказка для retry ----------------------------------------------------


def test_cjk_warning_russian_contains_required_phrase() -> None:
    warning = cjk_warning("ru")

    assert "Отвечай только на русском языке" in warning
    assert "Убери китайские иероглифы" in warning
    assert "ВНИМАНИЕ" in warning


def test_cjk_warning_english() -> None:
    warning = cjk_warning("en")

    assert "English only" in warning
    assert "Chinese characters" in warning
