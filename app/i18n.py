"""Локализация сообщений об ошибках API по `Accept-Language` (KI-058).

Контракт (SPEC §5): код ошибки в `code` не зависит от языка, а `detail`
отдаётся на языке, который предпочитает клиент. Переводим только известные
доменные коды: неизвестный код — это либо внутренняя ошибка (её текст не
раскрываем), либо сообщение, у которого перевода ещё нет, и тогда
возвращается исходный русский текст.

Выбор языка — тот же, что у комментариев/чата: первый поддерживаемый язык
в `Accept-Language` (`en-US,en;q=0.9` → `en`; `ru-RU` → `ru`), иначе
`ru`. Q-значения не переупорядочиваем: порядок в заголовке и есть
приоритет — так же считает :func:`app.prompts.resolve_language`.
"""

from __future__ import annotations

from typing import Final

from app.prompts import resolve_language

__all__ = [
    "ERROR_MESSAGES",
    "localize_error_detail",
    "prefers_english",
]

#: Переводы `detail` для известных кодов: код → {язык → сообщение}.
#: ``ru`` — текст по умолчанию (используется, если клиент не просит `en`).
ERROR_MESSAGES: Final[dict[str, dict[str, str]]] = {
    "email_not_verified": {
        "ru": (
            "Подтвердите email, чтобы пользоваться анализом и комментариями"
        ),
        "en": (
            "Email is not verified. Confirm your address using the link "
            "from the email."
        ),
    },
    "unauthorized": {
        "ru": "Требуется авторизация",
        "en": "Authentication required. Please sign in again.",
    },
    "forbidden": {
        "ru": "Недостаточно прав для этого действия",
        "en": "You do not have permission to perform this action.",
    },
    "not_found": {
        "ru": "Ресурс не найден",
        "en": "The requested resource was not found.",
    },
    "invalid_pgn": {
        "ru": "Не удалось разобрать PGN: проверьте нотацию ходов",
        "en": (
            "The PGN could not be parsed. Check the move notation and try "
            "again."
        ),
    },
    "invalid_fen": {
        "ru": "Некорректная позиция FEN",
        "en": "The FEN position is invalid.",
    },
    "invalid_level": {
        "ru": "Неизвестный уровень: ожидается beginner, club или gm",
        "en": "Unknown level. Expected one of: beginner, club, gm.",
    },
    "prompt_injection": {
        "ru": "Запрос заблокирован: обнаружена попытка prompt injection",
        "en": (
            "The request was blocked: a prompt injection attempt was "
            "detected."
        ),
    },
    "llm_not_configured": {
        "ru": "Для этого уровня не настроена LLM",
        "en": "The language model is not configured for this level.",
    },
    "llm_timeout": {
        "ru": "LLM не ответила за отведённое время",
        "en": "The language model did not respond in time. Please try again.",
    },
    "llm_invalid_output": {
        "ru": "LLM предложила нелегальные ходы; ответ не сохранён",
        "en": (
            "The language model produced invalid chess moves; the answer "
            "was not saved."
        ),
    },
    "llm_empty": {
        "ru": "Модель вернула пустой ответ",
        "en": "The language model returned an empty response.",
    },
    "engine_timeout": {
        "ru": "Шахматный движок не ответил за отведённое время",
        "en": "The chess engine did not respond in time. Please try again.",
    },
    "token_budget_exceeded": {
        "ru": "Дневной лимит токенов исчерпан",
        "en": "Daily token budget exceeded.",
    },
    "rate_limited": {
        "ru": "Слишком много запросов: попробуйте позже",
        "en": "Too many requests. Please try again later.",
    },
}


def prefers_english(accept_language: str | None) -> bool:
    """Предпочитает ли клиент английский по заголовку `Accept-Language`."""
    return resolve_language(None, accept_language, "ru") == "en"


def localize_error_detail(
    code: str,
    default_message: str,
    accept_language: str | None,
) -> str:
    """Локализовать `detail` ошибки по коду и языку клиента.

    Args:
        code: машинный код ошибки (`AppError.code`, `error`-событие SSE).
        default_message: исходное (русское) сообщение.
        accept_language: заголовок `Accept-Language` или ``None``.

    Returns:
        Английское сообщение для известного кода, если клиент просит `en`;
        иначе ``default_message`` без изменений.
    """
    if code == "internal_error" or not prefers_english(accept_language):
        return default_message
    messages = ERROR_MESSAGES.get(code)
    if messages is None:
        return default_message
    return messages["en"]
