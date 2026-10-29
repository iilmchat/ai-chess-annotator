"""FastAPI-зависимости аутентификации: пользователь из JWT.

Схема — `Authorization: Bearer <access-token>`. Refresh-токен здесь не
принимается: зависимости требуют claim ``type == "access"``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import TokenPayload, decode_token
from app.config import Settings, get_settings
from app.db.models import AdminRole, User
from app.db.session import get_session
from app.errors import AuthError, ForbiddenError

__all__ = ["get_current_user", "get_current_user_optional", "require_admin"]

#: auto_error=False: сами решаем, что вернуть (401 с нашим `code`).
_bearer = HTTPBearer(auto_error=False, description="JWT access token")


async def _load_user(
    token: str, session: AsyncSession, settings: Settings
) -> User:
    """Проверить access-токен и загрузить активного пользователя."""
    payload: TokenPayload = decode_token(token, settings, expected_type="access")
    user = await session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise AuthError("Пользователь не найден или отключён")
    return user


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    """Обязательная авторизация: валидный access-token активного пользователя.

    Raises:
        AuthError: заголовка нет, токен битый/просрочен/не того типа или
            пользователь отключён (→ HTTP 401).
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("Требуется авторизация: заголовок Authorization: Bearer")
    return await _load_user(credentials.credentials, session, settings)


async def get_current_user_optional(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User | None:
    """Мягкая авторизация: `None`, если токена нет или он невалиден.

    Для эндпоинтов, которые работают и анонимно (например, лимиты usage по
    IP), но при наличии валидного токена используют данные пользователя.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    try:
        return await _load_user(credentials.credentials, session, settings)
    except AuthError:
        return None


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Только роль `admin` (для админских действий публичного API).

    Raises:
        ForbiddenError: аутентифицирован, но роль не admin (→ HTTP 403).
    """
    if user.role != AdminRole.admin:
        raise ForbiddenError("Требуются права администратора")
    return user
