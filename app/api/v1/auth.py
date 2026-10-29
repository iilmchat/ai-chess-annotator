"""`/auth` — регистрация, вход, обновление токена и профиль (T-028).

Реализация эндпоинтов — в этом модуле; криптография и JWT — в `app/auth/`.
Роутер монтируется в `app/main.py` с префиксом `/auth` (вне `/v1`, как в
постановке).

Схема работы:

* `POST /auth/register` — создаёт пользователя (роль `user`, уровень `club`),
  пароль хранится только bcrypt-хешем;
* `POST /auth/login` — проверяет пару email/пароль, обновляет `last_login_at`
  и выдаёт пару access+refresh токенов;
* `POST /auth/refresh` — принимает ТОЛЬКО refresh-токен и выдаёт новую пару
  (ротация: у новых токенов новые `jti`);
* `GET /auth/me` — профиль по access-токену (зависимость
  :func:`app.auth.deps.get_current_user`).

Хеширование пароля — CPU-дорогая операция bcrypt, поэтому в event loop она
не выполняется: вызывается через :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.passwords import MAX_PASSWORD_BYTES, hash_password, verify_password
from app.auth.tokens import create_access_token, create_refresh_token, decode_token
from app.config import Settings, get_settings
from app.db.models import AdminRole, Level, User
from app.db.session import get_session
from app.errors import AuthError, ForbiddenError, UserExistsError

router = APIRouter(tags=["auth"])

#: Сообщение для неверной пары email/пароль (не раскрывает, что именно не так).
_INVALID_CREDENTIALS = "Неверный email или пароль"


class UserProfile(BaseModel):
    """Публичный профиль пользователя (без хеша пароля и служебных полей)."""

    id: int
    email: str
    level: Level
    role: AdminRole
    is_active: bool
    created_at: datetime


class TokenPair(BaseModel):
    """Пара JWT для клиента: короткий access + долгий refresh."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(
        ge=0, description="Время жизни access-токена в секундах"
    )
    user: UserProfile


class CredentialsRequest(BaseModel):
    """Тело регистрации/входа: email и пароль.

    Пароль ограничен 8..72 символами; дополнительно проверяется длина в
    байтах UTF-8 (bcrypt принимает максимум 72 байта — иначе он молча
    обрезал бы пароль, см. :data:`app.auth.passwords.MAX_PASSWORD_BYTES`).
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: Any) -> Any:
        """Привести email к нижнему регистру до валидации (регистр не значим)."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("password")
    @classmethod
    def _check_bcrypt_byte_limit(cls, value: str) -> str:
        """Отклонить пароль, который bcrypt не сможет захешировать целиком."""
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"Пароль длиннее {MAX_PASSWORD_BYTES} байт в кодировке UTF-8"
            )
        return value


class RegisterRequest(CredentialsRequest):
    """Тело `POST /auth/register`."""


class LoginRequest(CredentialsRequest):
    """Тело `POST /auth/login`."""


class RefreshRequest(BaseModel):
    """Тело `POST /auth/refresh` — только refresh-токен."""

    refresh_token: str = Field(min_length=1)


def _to_profile(user: User) -> UserProfile:
    """Преобразовать ORM-пользователя в публичный профиль."""
    return UserProfile(
        id=user.id,
        email=user.email,
        level=user.level,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def _issue_tokens(user: User, settings: Settings) -> TokenPair:
    """Выпустить новую пару токенов (у каждого свои `jti`)."""
    return TokenPair(
        access_token=create_access_token(user, settings),
        refresh_token=create_refresh_token(user, settings),
        token_type="bearer",
        expires_in=settings.access_token_ttl_min * 60,
        user=_to_profile(user),
    )


@router.post(
    "/register",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация по email и паролю",
)
async def register(
    payload: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserProfile:
    """Создать пользователя и вернуть его профиль (201).

    Raises:
        UserExistsError: email уже занят (→ HTTP 409, код `email_taken`).
    """
    existing = await session.scalar(
        select(User).where(User.email == payload.email)
    )
    if existing is not None:
        raise UserExistsError("Пользователь с таким email уже зарегистрирован")

    password_hash = await asyncio.to_thread(hash_password, payload.password)
    user = User(
        email=payload.email,
        level=Level.club,
        role=AdminRole.user,
        is_active=True,
        password_hash=password_hash,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Гонка двух одновременных регистраций одного email: pre-check не
        # спас, уникальный индекс БД — последняя линия обороны.
        await session.rollback()
        raise UserExistsError(
            "Пользователь с таким email уже зарегистрирован"
        ) from None
    await session.refresh(user)
    return _to_profile(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Вход по email и паролю",
)
async def login(
    payload: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenPair:
    """Проверить пароль, обновить `last_login_at` и выдать пару токенов.

    Raises:
        AuthError: неверный email или пароль (→ HTTP 401, код `unauthorized`).
        ForbiddenError: учётная запись отключена (→ HTTP 403, код `forbidden`).
    """
    user = await session.scalar(
        select(User).where(User.email == payload.email)
    )
    if user is None:
        raise AuthError(_INVALID_CREDENTIALS)
    password_ok = await asyncio.to_thread(
        verify_password, payload.password, user.password_hash
    )
    if not password_ok:
        raise AuthError(_INVALID_CREDENTIALS)
    if not user.is_active:
        raise ForbiddenError("Учётная запись отключена")
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return _issue_tokens(user, settings)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Обновить пару токенов по refresh-токену",
)
async def refresh(
    payload: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenPair:
    """Проверить refresh-токен и выдать новую пару (ротация).

    Access-токен в этом поле не принимается: `decode_token` вызывается с
    ``expected_type="refresh"``.

    Raises:
        AuthError: токен битый/просрочен/не refresh или пользователь
            отключён (→ HTTP 401, код `unauthorized`).
    """
    claims = decode_token(
        payload.refresh_token, settings, expected_type="refresh"
    )
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthError("Пользователь не найден или отключён")
    return _issue_tokens(user, settings)


@router.get(
    "/me",
    response_model=UserProfile,
    summary="Профиль текущего пользователя",
)
async def me(
    user: Annotated[User, Depends(get_current_user)],
) -> UserProfile:
    """Вернуть профиль пользователя из access-токена.

    Raises:
        AuthError: заголовка `Authorization: Bearer` нет или токен невалиден
            (→ HTTP 401, код `unauthorized`).
    """
    return _to_profile(user)
