"""`/auth` — регистрация, вход, обновление/отзыв токенов и профиль.

Реализация эндпоинтов — в этом модуле; криптография, cookie и JWT — в
`app/auth/`. Роутер монтируется в `app/main.py` с префиксом `/auth` (вне
`/v1`, как в постановке).

Схема работы (T-028 + KI-036/KI-037):

* `POST /auth/register` — создаёт пользователя; пароль хранится Argon2id-хешем
  (`$argon2id$`);
* `POST /auth/login` — проверяет email/пароль, обновляет `last_login_at`,
  при необходимости **перехеширует** legacy bcrypt → Argon2id в той же
  транзакции; для неизвестного email всё равно выполняет Argon2 verify по
  dummy-хешу (KI-043, timing attack), возвращает access-токен в JSON, а
  refresh — в HttpOnly-cookie;
* `POST /auth/refresh` — принимает refresh из cookie (fallback — тело JSON для
  curl/не-браузерных клиентов), проверяет Redis-denylist, **ротирует** cookie
  и выдаёт новый access; новый refresh получает новый `jti`;
* `POST /auth/logout` — серверный отзыв refresh (jti → Redis denylist) и
  очистка cookie; 204 в любом случае (битый токен/недоступный Redis — не
  ошибка клиента, см. `app/auth/denylist.py`);
* `GET /auth/me` — профиль по access-токену.

Хеширование/проверка Argon2id — CPU-дорогая операция, поэтому в event loop
она не блокирует: вызывается через :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import clear_refresh_cookie, set_refresh_cookie
from app.auth.denylist import is_refresh_token_revoked, revoke_refresh_token
from app.auth.deps import get_current_user
from app.auth.passwords import (
    DUMMY_PASSWORD_HASH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_and_update,
    verify_password,
)
from app.auth.tokens import create_access_token, create_refresh_token, decode_token
from app.config import Settings, get_settings
from app.db.models import AdminRole, Level, User
from app.db.session import get_session
from app.errors import AuthError, ForbiddenError, UserExistsError

router = APIRouter(tags=["auth"])

logger = structlog.get_logger(__name__)

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


class AccessTokenResponse(BaseModel):
    """Ответ `/auth/login` и `/auth/refresh`: access JWT + профиль.

    Refresh-токена здесь нет намеренно (KI-037): он живёт в HttpOnly-cookie
    и не должен быть доступен JavaScript.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(
        ge=0, description="Время жизни access-токена в секундах"
    )
    user: UserProfile


class CredentialsRequest(BaseModel):
    """Тело регистрации/входа: email и пароль.

    Пароль ограничен 8..256 символами (Argon2id не ограничен 72 байтами
    bcrypt — верхняя граница чисто прикладная, чтобы KDF не считал мегабайты).
    """

    email: EmailStr
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: Any) -> Any:
        """Привести email к нижнему регистру до валидации (регистр не значим)."""
        if isinstance(value, str):
            return value.strip().lower()
        return value


class RegisterRequest(CredentialsRequest):
    """Тело `POST /auth/register`."""


class LoginRequest(CredentialsRequest):
    """Тело `POST /auth/login`."""


class RefreshRequest(BaseModel):
    """Тело `POST /auth/refresh`/`/auth/logout` (fallback для non-browser)."""

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


def _issue_tokens(
    user: User, settings: Settings
) -> tuple[AccessTokenResponse, str]:
    """Выпустить access (в тело ответа) и refresh (в cookie) токены.

    Каждый вызов генерирует новые `jti` — это и есть ротация refresh-токена.
    """
    access_token = create_access_token(user, settings)
    refresh_token = create_refresh_token(user, settings)
    return (
        AccessTokenResponse(
            access_token=access_token,
            token_type="bearer",
            expires_in=settings.access_token_ttl_min * 60,
            user=_to_profile(user),
        ),
        refresh_token,
    )


def _redis_client(request: Request) -> aioredis.Redis | None:
    """Redis-клиент из `app.state` (создаётся в lifespan) либо None."""
    # state наполняется в lifespan; в юнит-тестах без приложения атрибута нет.
    return cast(
        "aioredis.Redis | None",
        getattr(request.app.state, "redis", None),
    )


def _submitted_refresh_token(
    request: Request, settings: Settings, payload: RefreshRequest | None
) -> str | None:
    """Refresh-токен из cookie (браузер) либо из JSON-тела (fallback).

    Cookie в приоритете: браузер всегда шлёт актуальный токен, а тело — это
    явный fallback для curl/CLI, которым cookie-джар не нужен.
    """
    cookie_token = request.cookies.get(settings.auth_cookie_name)
    if cookie_token:
        return cookie_token
    if payload is not None and payload.refresh_token:
        return payload.refresh_token
    return None


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
    response_model=AccessTokenResponse,
    summary="Вход по email и паролю",
)
async def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AccessTokenResponse:
    """Проверить пароль, обновить `last_login_at` и выдать токены.

    Legacy bcrypt-хеш при успешном входе заменяется на Argon2id в той же
    транзакции (событие `password_rehashed` в structlog-логах). Refresh
    уходит HttpOnly-cookie, access — в JSON.

    Raises:
        AuthError: неверный email или пароль (→ HTTP 401, код `unauthorized`).
        ForbiddenError: учётная запись отключена (→ HTTP 403, код `forbidden`).
    """
    user = await session.scalar(
        select(User).where(User.email == payload.email)
    )
    if user is None:
        # KI-043 (timing attack): выравниваем время ответа с веткой
        # существующего email — Argon2 verify по dummy-хешу выполняется и
        # здесь, поэтому 401 приходит примерно за то же время. Пароль
        # dummy-хешу не подходит, результат (False) намеренно игнорируется.
        await asyncio.to_thread(
            verify_password, payload.password, DUMMY_PASSWORD_HASH
        )
        raise AuthError(_INVALID_CREDENTIALS)
    password_ok, upgraded_hash = await asyncio.to_thread(
        verify_and_update, payload.password, user.password_hash
    )
    if not password_ok:
        raise AuthError(_INVALID_CREDENTIALS)
    if not user.is_active:
        raise ForbiddenError("Учётная запись отключена")
    if upgraded_hash is not None:
        user.password_hash = upgraded_hash
        logger.info(
            "password_rehashed",
            user_id=user.id,
            algorithm="argon2id",
            previous_algorithm="bcrypt",
        )
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    tokens, refresh_token = _issue_tokens(user, settings)
    set_refresh_cookie(response, refresh_token, settings)
    return tokens


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Обновить access-токен по refresh-токену",
)
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    payload: RefreshRequest | None = None,
) -> AccessTokenResponse:
    """Проверить refresh-токен (cookie или тело), denylist и выдать новый access.

    Ротация: ответ содержит новый access и новый refresh-cookie (новый `jti`);
    прежний refresh-токен попадает в Redis-denylist и повторно не принимается
    (защита от replay украденного токена, KI-037).

    Raises:
        AuthError: токена нет, он битый/просрочен/не refresh/отозван или
            пользователь отключён (→ HTTP 401, код `unauthorized`).
    """
    token = _submitted_refresh_token(request, settings, payload)
    if token is None:
        raise AuthError("Требуется refresh-токен")
    claims = decode_token(token, settings, expected_type="refresh")
    redis_client = _redis_client(request)
    if redis_client is not None and settings.refresh_denylist_enabled:
        # Отозванный (logout) refresh не должен давать новую пару.
        if await is_refresh_token_revoked(redis_client, claims.jti):
            raise AuthError("Refresh-токен отозван")
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthError("Пользователь не найден или отключён")
    tokens, refresh_token = _issue_tokens(user, settings)
    # KI-037: ротация должна инвалидировать прежний refresh (защита от replay
    # украденного токена). Старый jti уходит в denylist; fail-open не даёт
    # logout/refresh падать при недоступном Redis.
    redis_client = _redis_client(request)
    if redis_client is not None and settings.refresh_denylist_enabled:
        await revoke_refresh_token(redis_client, claims)
    set_refresh_cookie(response, refresh_token, settings)
    return tokens


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Выход: отозвать refresh-токен и очистить cookie",
)
async def logout(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    payload: RefreshRequest | None = None,
) -> Response:
    """Отозвать refresh-токен (denylist) и удалить cookie; всегда 204.

    Битый/просроченный/отсутствующий токен — не ошибка: cookie всё равно
    очищается. Недоступный Redis — fail-open (warning в логах, 204), см.
    `app/auth/denylist.py`.
    """
    token = _submitted_refresh_token(request, settings, payload)
    if token and settings.refresh_denylist_enabled:
        try:
            claims = decode_token(token, settings, expected_type="refresh")
        except AuthError:
            # Битый/чужой/просроченный refresh — отзывать нечего.
            claims = None
        redis_client = _redis_client(request)
        if claims is not None and redis_client is not None:
            await revoke_refresh_token(redis_client, claims)
    clear_refresh_cookie(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


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
