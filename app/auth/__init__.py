"""Аутентификация и авторизация: пароли, JWT, cookie и зависимости (T-028).

Модуль не знает про конкретные эндпоинты: API-слой (`app/api/v1/auth.py`)
использует утилиты отсюда, а защищённые роутеры — зависимости
:func:`get_current_user` / :func:`require_admin`.

KI-037: refresh-токен ездит в HttpOnly-cookie (`app.auth.cookies`), а его
отзыв при logout хранится в Redis (`app.auth.denylist`). Новые пароли —
Argon2id, legacy bcrypt проверяется и мигрирует при входе
(`app.auth.passwords.verify_and_update`).
"""

from __future__ import annotations

from app.auth.access import ensure_game_access, get_accessible_game
from app.auth.cookies import (
    clear_refresh_cookie,
    refresh_cookie_max_age_s,
    set_refresh_cookie,
)
from app.auth.denylist import (
    denylist_key,
    is_refresh_token_revoked,
    revoke_refresh_token,
)
from app.auth.deps import (
    get_current_user,
    get_current_user_optional,
    require_admin,
)
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_and_update,
    verify_password,
)
from app.auth.tokens import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_token,
)

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "TokenPayload",
    "clear_refresh_cookie",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "denylist_key",
    "ensure_game_access",
    "get_accessible_game",
    "get_current_user",
    "get_current_user_optional",
    "hash_password",
    "is_refresh_token_revoked",
    "refresh_cookie_max_age_s",
    "require_admin",
    "revoke_refresh_token",
    "set_refresh_cookie",
    "verify_and_update",
    "verify_password",
]
