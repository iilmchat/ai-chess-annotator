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
    "create_email_verification_token",
    "create_password_reset_token",
    "create_refresh_token",
    "decode_token",
]

TokenType = Literal[
    "access", "refresh", "email_verify", "password_reset"
]

#: Все типы, которые принимает `decode_token` (refresh/access — API,
#: email_verify/password_reset — одноразовые действия из письма).
_TOKEN_TYPES = frozenset(
    {"access", "refresh", "email_verify", "password_reset"}
)

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
    #: Точное время выпуска (из `iat_ms`; fallback — `iat` в секундах).
    #: Нужно global revocation: сравнение секунд давало бы гонку «токен
    #: выпущен в ту же секунду, что и logout-all».
    issued_at: datetime | None = None


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
        # Расширение W3C-независимого JWT: миллисекунды для сравнения с
        # `revoked_before:<user_id>` (см. app.auth.denylist).
        "iat_ms": int(now.timestamp() * 1000),
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


def create_email_verification_token(user: User, settings: Settings) -> str:
    """Одноразовая ссылка подтверждения email (короткий TTL)."""
    return _encode(
        user,
        "email_verify",
        timedelta(hours=settings.email_verification_ttl_hours),
        settings,
    )


def create_password_reset_token(user: User, settings: Settings) -> str:
    """Одноразовая ссылка сброса пароля (короткий TTL)."""
    return _encode(
        user,
        "password_reset",
        timedelta(minutes=settings.password_reset_ttl_min),
        settings,
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
        or token_type not in _TOKEN_TYPES
        or not isinstance(jti, str)
        or not isinstance(exp, (int, float))
    ):
        raise AuthError("Некорректная полезная нагрузка токена")
    if expected_type is not None and token_type != expected_type:
        raise AuthError(f"Требуется токен типа {expected_type}")
    issued_at = _issued_at(raw)
    return TokenPayload(
        user_id=int(sub),
        role=role,
        token_type=token_type,
        jti=jti,
        expires_at=datetime.fromtimestamp(float(exp), UTC),
        issued_at=issued_at,
    )


def _issued_at(raw: dict[str, object]) -> datetime | None:
    """Время выпуска: `iat_ms` (точнее) либо `iat`; иначе None (legacy)."""
    raw_ms = raw.get("iat_ms")
    if isinstance(raw_ms, (int, float)):
        return datetime.fromtimestamp(float(raw_ms) / 1000, UTC)
    raw_seconds = raw.get("iat")
    if isinstance(raw_seconds, (int, float)):
        return datetime.fromtimestamp(float(raw_seconds), UTC)
    return None
