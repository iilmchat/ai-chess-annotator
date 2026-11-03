"""Unit-тесты буферизующего CJK-фильтра SSE (KI-060, итерация 53).

Фильтр проверяется напрямую, без сети и БД: важно, что ни один кусок,
отдаваемый в `delta`, не содержит иероглифов U+4E00–U+9FFF, и что очистка
не ломает обычный русский/английский текст.
"""

from __future__ import annotations

import pytest

from app.commentary.streaming import (
    MAX_DELTA_CHARS,
    MIN_DELTA_CHARS,
    CjkStreamBuffer,
    has_cjk,
)

CJK_SENTENCE = "这是中文。"


def _run(chunks: list[str], **kwargs: int) -> tuple[str, CjkStreamBuffer, list[str]]:
    """Прогнать чанки через буфер и вернуть текст, буфер и сырые куски."""
    buffer = CjkStreamBuffer(**kwargs)  # type: ignore[arg-type]
    pieces: list[str] = []
    for chunk in chunks:
        pieces.extend(buffer.feed(chunk))
    pieces.extend(buffer.flush())
    return "".join(pieces), buffer, pieces


def test_holds_text_until_sentence_end() -> None:
    buffer = CjkStreamBuffer()
    assert buffer.feed("Партия ") == []
    assert buffer.feed("началась с d4.") == ["Партия началась с d4."]
    assert buffer.pending == ""


def test_cuts_long_text_without_punctuation_by_words() -> None:
    text = "Слово " * 12
    buffer = CjkStreamBuffer()
    pieces = buffer.feed(text) + buffer.flush()
    assert "".join(pieces) == text
    assert all(len(piece) <= MAX_DELTA_CHARS for piece in pieces)
    assert all(len(piece) >= MIN_DELTA_CHARS - 1 for piece in pieces[:-1])
    assert not any(has_cjk(piece) for piece in pieces)


def test_drops_cjk_sentence_but_keeps_clean_neighbours() -> None:
    text = "Партия началась с d4. " + CJK_SENTENCE + " Затем Nf6."
    result, buffer, pieces = _run([text[i : i + 3] for i in range(0, len(text), 3)])
    assert not any(has_cjk(piece) for piece in pieces)
    # Пробелы на стыке вырезанного предложения не фиксируем: результат
    # зависит от границ чанков, важно отсутствие иероглифов и сохранность
    # соседних предложений.
    assert " ".join(result.split()) == "Партия началась с d4. Затем Nf6."
    assert buffer.stripped_cjk_chars == 4
    assert buffer.stripped_units == 1


def test_cjk_split_across_chunks_never_leaks() -> None:
    chunks = ["Партия ", "началась с d4. ", "这", "是中", "文。", " Затем Nf6."]
    result, buffer, pieces = _run(chunks)
    assert not any(has_cjk(piece) for piece in pieces)
    assert "d4" in result and "Nf6" in result
    assert buffer.stripped_cjk_chars == 4


def test_cjk_char_split_from_word_by_chunk_boundary() -> None:
    """Иероглиф в середине слова отдельным чанком не должен утечь."""
    result, _buffer, pieces = _run(["Партия ", "d4 ", "马", "e4. Затем Nf6."])
    assert not any(has_cjk(piece) for piece in pieces)
    assert "马" not in result
    assert "Nf6" in result


def test_all_cjk_returns_nothing() -> None:
    result, buffer, pieces = _run(["这是", "中文。"])
    assert pieces == []
    assert result == ""
    assert buffer.stripped_cjk_chars == 4


def test_flush_returns_pending_tail() -> None:
    result, _buffer, pieces = _run(["Короткий хвост"])
    assert pieces == ["Короткий хвост"]
    assert result == "Короткий хвост"


def test_cjk_in_tail_is_cut_on_flush() -> None:
    result, buffer, pieces = _run(["Чистое предложение. 最后"])
    assert not any(has_cjk(piece) for piece in pieces)
    assert result == "Чистое предложение. "
    assert buffer.stripped_cjk_chars == 2


def test_non_cjk_unicode_is_preserved() -> None:
    """Диапазон фильтра — только U+4E00–U+9FFF (KI-060).

    Хирагана и расширение A вне диапазона не считаются дрейфом и не
    вырезаются; это осознанное ограничение существующего детектора.
    """
    text = "Ход ひらがな и 㐀 (ext A) — не CJK-дрейф по KI-060."
    result, buffer, pieces = _run([text])
    assert result == text
    assert pieces == [text]
    assert buffer.stripped_cjk_chars == 0


def test_empty_chunks_are_ignored() -> None:
    result, _buffer, pieces = _run(["", "Партия d4.", ""])
    assert pieces == ["Партия d4."]
    assert result == "Партия d4."


def test_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        CjkStreamBuffer(min_chars=0)
    with pytest.raises(ValueError):
        CjkStreamBuffer(min_chars=10, max_chars=5)


def test_delta_stream_contains_no_cjk_char_by_char() -> None:
    """Посимвольная подача: ни один delta-кусок не содержит иероглифов."""
    text = (
        "Дебют был спокойным. 这是第一步。 Затем белые сыграли d4, "
        "а чёрные ответили Nf6. 最终局面。"
    )
    result, buffer, pieces = _run(list(text))
    assert not any(has_cjk(piece) for piece in pieces)
    assert "d4" in result and "Nf6" in result
    assert buffer.stripped_cjk_chars >= 8


def test_cjk_terminator_without_space_is_boundary() -> None:
    """Китайская типографика: ``。`` без пробела после — тоже граница."""
    result, buffer, pieces = _run(["这是中文。Затем Nf6."])
    assert not any(has_cjk(piece) for piece in pieces)
    assert result == "Затем Nf6."
    assert buffer.stripped_cjk_chars == 4


def test_long_cjk_run_without_punctuation_never_leaks() -> None:
    """CJK-ран длиннее MAX_DELTA не должен отдаваться сырым по размеру."""
    cjk_run = "马" * 100
    result, buffer, pieces = _run([cjk_run[i : i + 7] for i in range(0, 100, 7)])
    assert pieces == []
    assert result == ""
    assert buffer.pending == ""
    assert buffer.stripped_cjk_chars == 100


def test_crlf_paragraphs_and_cjk_survive() -> None:
    text = "Первый абзац.\r\n这是。\r\nВторой абзац с d4."
    result, _buffer, pieces = _run(text.splitlines(keepends=True))
    assert not any(has_cjk(piece) for piece in pieces)
    assert "Первый абзац." in result
    assert "Второй абзац с d4." in result


def test_cjk_sentence_with_san_move_is_dropped_entirely() -> None:
    """Предложение с иероглифом режется целиком (как в service.py)."""
    result, _buffer, pieces = _run(["Ход d4 是 лучший. Затем Nf6."])
    assert not any(has_cjk(piece) for piece in pieces)
    assert "Затем Nf6." in result
    assert "d4" not in result

