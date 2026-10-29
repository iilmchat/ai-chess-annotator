"""Доменные исключения ChessAnnotator.

Собственные исключения живут здесь (RULES.md §3.1): API-слой (T-009) маппит
их в HTTP-коды, а модули (`ingestion`, `engine`, `policy`, `validator`) не
знают про FastAPI.

Каждое исключение несёт:

* ``code`` — машиночитаемый идентификатор для API/логов;
* ``http_status`` — предлагаемый HTTP-код (используется обработчиком в T-009);
* ``message`` — текст для пользователя.
"""

from __future__ import annotations

__all__ = [
    "AppError",
    "AuthError",
    "EmbeddingError",
    "EngineError",
    "EngineNotFoundError",
    "EngineTimeoutError",
    "ForbiddenError",
    "InvalidFenError",
    "InvalidLevelError",
    "InvalidPgnError",
    "LLMError",
    "LLMNotConfiguredError",
    "LLMTimeoutError",
    "LLMValidationError",
    "NotFoundError",
    "PdfExportError",
    "PdfFontUnavailableError",
    "PromptInjectionError",
    "UserExistsError",
]


class AppError(Exception):
    """Базовое доменное исключение проекта."""

    code: str = "app_error"
    http_status: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPgnError(AppError):
    """PGN не распознан, содержит нелегальный ход или не содержит ходов."""

    code = "invalid_pgn"
    http_status = 400


class InvalidFenError(AppError):
    """FEN не является синтаксически корректной и/или возможной позицией."""

    code = "invalid_fen"
    http_status = 400


class InvalidLevelError(AppError):
    """Уровень ассистента не из набора beginner / club / gm."""

    code = "invalid_level"
    http_status = 422


class EmbeddingError(AppError):
    """Эмбеддинг недоступен: сеть/HTTP/таймаут, битый ответ или размерность.

    Размерность вектора модели не совпала с `annotated_games.embedding`
    (`vector(768)`, KI-025) — тоже эта ошибка: писать такой вектор в БД
    бессмысленно, PostgreSQL вернул бы невнятную ошибку приведения.
    """

    code = "embedding_unavailable"
    http_status = 503


class EngineError(AppError):
    """Базовый класс ошибок движка (Stockfish недоступен или сломан)."""

    code = "engine_error"
    http_status = 500


class EngineNotFoundError(EngineError):
    """Бинарник Stockfish не найден по пути из настроек (см. KI-006)."""

    code = "engine_not_found"
    http_status = 500


class EngineTimeoutError(EngineError):
    """Движок не уложился в отведённое время и был перезапущен."""

    code = "engine_timeout"
    http_status = 504


class LLMError(AppError):
    """Базовый класс ошибок LLM-провайдеров (T-013)."""

    code = "llm_error"
    http_status = 503


class LLMNotConfiguredError(LLMError):
    """Для выбранного уровня не задан API-ключ / провайдер."""

    code = "llm_not_configured"
    http_status = 503


class LLMTimeoutError(LLMError):
    """LLM не ответила в отведённый таймаут."""

    code = "llm_timeout"
    http_status = 504


class LLMValidationError(LLMError):
    """Ответ LLM содержит нелегальные ходы даже после retry (T-016)."""

    code = "llm_invalid_output"
    http_status = 502


class NotFoundError(AppError):
    """Запрошенный ресурс (партия, комментарий) не найден."""

    code = "not_found"
    http_status = 404


class AuthError(AppError):
    """Пользователь не аутентифицирован: нет/битый/просроченный JWT (T-028).

    Всегда 401: клиент должен очистить токены и показать форму входа.
    """

    code = "unauthorized"
    http_status = 401


class ForbiddenError(AppError):
    """Аутентифицирован, но прав недостаточно (например, не admin) — 403."""

    code = "forbidden"
    http_status = 403


class UserExistsError(AppError):
    """Регистрация на уже занятый email (T-028) — 409."""

    code = "email_taken"
    http_status = 409


class PromptInjectionError(AppError):
    """Входные данные содержат попытку prompt injection (T-033)."""

    code = "prompt_injection"
    http_status = 422

    def __init__(self, message: str, *, rule_ids: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.rule_ids = rule_ids


class PdfExportError(AppError):
    """Не удалось сформировать PDF комментария (T-107)."""

    code = "pdf_export_failed"
    http_status = 500


class PdfFontUnavailableError(PdfExportError):
    """Не найден TrueType-шрифт с кириллицей для PDF (окружение/конфиг)."""

    code = "pdf_font_unavailable"
    http_status = 503
