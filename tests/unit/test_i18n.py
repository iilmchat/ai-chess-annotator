"""KI-058: локализация `detail` ошибок API по `Accept-Language`.

Проверяем сам помощник без HTTP: выбор языка (включая варианты заголовка),
известные коды, неизвестный код и запрет перевода `internal_error`.
"""

from __future__ import annotations

import pytest

from app.i18n import (
    ERROR_MESSAGES,
    localize_error_detail,
    prefers_english,
)

#: Коды, для которых перевод обязателен по постановке KI-058.
REQUIRED_CODES = (
    "email_not_verified",
    "unauthorized",
    "forbidden",
    "not_found",
    "invalid_pgn",
    "invalid_fen",
    "invalid_level",
    "prompt_injection",
    "llm_not_configured",
    "llm_timeout",
    "llm_invalid_output",
    "engine_timeout",
    "token_budget_exceeded",
)


@pytest.mark.parametrize("code", REQUIRED_CODES)
def test_required_codes_have_ru_and_en_messages(code: str) -> None:
    assert code in ERROR_MESSAGES
    assert ERROR_MESSAGES[code]["ru"]
    assert ERROR_MESSAGES[code]["en"]
    assert ERROR_MESSAGES[code]["ru"] != ERROR_MESSAGES[code]["en"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("en", True),
        ("en-US", True),
        ("en-US,en;q=0.9", True),
        ("EN-us", True),
        ("ru", False),
        ("ru-RU,ru;q=0.9", False),
        ("ru,en;q=0.9", False),
        ("de,fr;q=0.9", False),
        ("", False),
        (None, False),
    ],
)
def test_prefers_english(header: str | None, expected: bool) -> None:
    assert prefers_english(header) is expected


def test_known_code_localized_to_english() -> None:
    detail = localize_error_detail(
        "email_not_verified",
        "Подтвердите email, чтобы пользоваться анализом",
        "en-US,en;q=0.9",
    )

    assert detail == ERROR_MESSAGES["email_not_verified"]["en"]


@pytest.mark.parametrize("header", [None, "ru", "ru-RU,ru;q=0.9"])
def test_default_language_keeps_russian_message(header: str | None) -> None:
    detail = localize_error_detail(
        "email_not_verified",
        "Подтвердите email, чтобы пользоваться анализом",
        header,
    )

    assert detail == "Подтвердите email, чтобы пользоваться анализом"


def test_unknown_code_falls_back_to_original_message() -> None:
    detail = localize_error_detail(
        "some_new_code", "Исходное сообщение", "en"
    )

    assert detail == "Исходное сообщение"


def test_internal_error_is_never_translated() -> None:
    detail = localize_error_detail("internal_error", "Внутренняя ошибка", "en")

    assert detail == "Внутренняя ошибка"


def test_header_with_ru_first_wins_over_english() -> None:
    detail = localize_error_detail("not_found", "Не найдено", "ru,en;q=0.9")

    assert detail == "Не найдено"


def test_malformed_header_falls_back_to_russian() -> None:
    assert localize_error_detail("not_found", "Не найдено", ";;,") == "Не найдено"
