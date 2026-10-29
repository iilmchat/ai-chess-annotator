"""Аутентификация и авторизация: пароли, JWT и FastAPI-зависимости (T-028).

Модуль не знает про конкретные эндпоинты: API-слой (`app/api/v1/auth.py`)
использует утилиты отсюда, а защищённые роутеры — зависимости
:func:`get_current_user` / :func:`require_admin`.
"""

from __future__ import annotations

from app.auth.access import ensure_game_access, get_accessible_game
from app.auth.deps import (
    get_current_user,
    get_current_user_optional,
    require_admin,
)
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_token,
)

__all__ = [
    "TokenPayload",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "ensure_game_access",
    "get_accessible_game",
    "get_current_user",
    "get_current_user_optional",
    "hash_password",
    "require_admin",
    "verify_password",
]
