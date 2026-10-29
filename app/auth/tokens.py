"""Генерация и проверка JWT (python-jose).

Access- и refresh-токены различаются claim-ом ``type``; в полезной
нагрузке есть ``sub`` (id пользователя), ``role``, ``jti`` и ``exp``.
Refresh-токен НЕ даёт доступа к API: зависимости проверяют
``expected_type="access"``, а `/auth/refresh` принимает только refresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from jose import JWTError, jwt

from app.config import Settings
from app.db.models import User
from app.errors import AuthError

__all__ = [
    "TokenPayload",
    "TokenType",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
]

TokenType = Literal["access", "refresh"]

#: Минимальная длина JWT_SECRET, при которой подпись имеет смысл.
MIN_SECRET_LENGTH = 16


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Разобранная полезная нагрузка JWT."""

    user_id: int
    role: str
    token_type: TokenType
    jti: str
    expires_at: datetime


def _encode(
    user: User, token_type: TokenType, ttl: timedelta, settings: Settings
) -> str:
    """Собрать и подписать токен указанного типа."""
    if len(settings.jwt_secret) < MIN_SECRET_LENGTH:
        raise AuthError("JWT_SECRET короче минимальной длины — вход недоступен")
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": now + ttl,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def create_access_token(user: User, settings: Settings) -> str:
    """Короткий токен для доступа к защищённым эндпоинтам."""
    return _encode(
        user, "access", timedelta(minutes=settings.access_token_ttl_min), settings
    )


def create_refresh_token(user: User, settings: Settings) -> str:
    """Долгий токен только для `/auth/refresh`."""
    return _encode(
        user, "refresh", timedelta(days=settings.refresh_token_ttl_days), settings
    )


def decode_token(
    token: str, settings: Settings, *, expected_type: TokenType | None = None
) -> TokenPayload:
    """Проверить подпись/срок и разобрать токен.

    Raises:
        AuthError: токен битый, просрочен, неверного типа или без обязательных
            claim-ов (→ HTTP 401).
    """
    try:
        raw = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError as exc:
        raise AuthError("Недействительный или истёкший токен") from exc

    sub = raw.get("sub")
    role = raw.get("role")
    token_type = raw.get("type")
    jti = raw.get("jti")
    exp = raw.get("exp")
    if (
        not isinstance(sub, str)
        or not sub.isdigit()
        or not isinstance(role, str)
        or token_type not in {"access", "refresh"}
        or not isinstance(jti, str)
        or not isinstance(exp, (int, float))
    ):
        raise AuthError("Некорректная полезная нагрузка токена")
    if expected_type is not None and token_type != expected_type:
        raise AuthError(f"Требуется токен типа {expected_type}")
    return TokenPayload(
        user_id=int(sub),
        role=role,
        token_type=token_type,
        jti=jti,
        expires_at=datetime.fromtimestamp(float(exp), UTC),
    )
