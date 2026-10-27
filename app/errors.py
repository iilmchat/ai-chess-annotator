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

__all__ = ["AppError", "InvalidFenError", "InvalidPgnError"]


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
