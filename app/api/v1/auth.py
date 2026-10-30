"""`/auth` — регистрация, вход, обновление/отзыв токенов и профиль.

Реализация эндпоинтов — в этом модуле; криптография, cookie и JWT — в
`app/auth/`. Роутер монтируется в `app/main.py` с префиксом `/auth` (вне
`/v1`, как в постановке).

Схема работы (T-028 + KI-036/KI-037/KI-041):

* `POST /auth/register` — создаёт пользователя (`is_verified=false`) и
  отправляет письмо со ссылкой подтверждения (MockEmailSender;
  `$argon2id$`-хеш пароля);
* `GET /auth/verify-email` — валидирует `email_verify` JWT и ставит
  `is_verified=true`;
* `POST /auth/login` — проверяет email/пароль, обновляет `last_login_at`,
  при необходимости **перехеширует** legacy bcrypt → Argon2id в той же
  транзакции; для неизвестного email всё равно выполняет Argon2 verify по
  dummy-хешу (KI-043, timing attack), возвращает access-токен в JSON, а
  refresh — в HttpOnly-cookie;
* `POST /auth/refresh` — принимает refresh из cookie (fallback — тело JSON для
  curl/не-браузерных клиентов), проверяет Redis-denylist и глобальный
  отзыв (`revoked_before:<user_id>`), **ротирует** cookie и выдаёт новый
  access; новый refresh получает новый `jti`;
* `POST /auth/logout` — серверный отзыв refresh (jti → Redis denylist) и
  очистка cookie; 204 в любом случае (битый токен/недоступный Redis — не
  ошибка клиента, см. `app/auth/denylist.py`);
* `POST /auth/logout-all` — `revoked_before:<user_id>` + очистка cookie:
  все refresh, выпущенные раньше, перестают работать (KI-041);
* `POST /auth/forgot-password` — письмо со ссылкой сброса (202 всегда,
  без раскрытия существования email);
* `POST /auth/reset-password` — меняет хеш, отзывает старые refresh и
  делает токен одноразовым;
* `GET /auth/me` — профиль по access-токену;
* `PATCH /auth/me` — правка профиля (`display_name`, `level`, T-082);
* `POST /auth/change-password` — смена пароля по текущему паролю (T-082):
  хеш перезаписывается, все refresh-сессии отзываются.

Хеширование/проверка Argon2id — CPU-дорогая операция, поэтому в event loop
она не блокирует: вызывается через :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import clear_refresh_cookie, set_refresh_cookie
from app.auth.denylist import (
    is_refresh_token_revoked,
    is_token_globally_revoked,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
)
from app.auth.deps import get_current_user
from app.auth.passwords import (
    DUMMY_PASSWORD_HASH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_and_update,
    verify_password,
)
from app.auth.tokens import (
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
)
from app.config import Settings, get_settings
from app.db.models import AdminRole, Level, User, UserTier
from app.db.session import get_session
from app.email import (
    EmailSender,
    get_email_sender,
    password_reset_message,
    verification_message,
)
from app.errors import AuthError, ForbiddenError, UserExistsError

router = APIRouter(tags=["auth"])

logger = structlog.get_logger(__name__)

#: Сообщение для неверной пары email/пароль (не раскрывает, что именно не так).
_INVALID_CREDENTIALS = "Неверный email или пароль"

#: Максимальная длина display_name (T-082) — совпадает с `users.display_name`.
_DISPLAY_NAME_MAX_LENGTH = 120


class UserProfile(BaseModel):
    """Публичный профиль пользователя (без хеша пароля и служебных полей)."""

    id: int
    email: str
    #: T-082: необязательное отображаемое имя; `None` — не задано.
    display_name: str | None
    level: Level
    role: AdminRole
    #: T-083: тариф (`free`/`pro`); меняется Stripe webhook-ом.
    tier: UserTier
    is_active: bool
    #: KI-041: подтверждён ли email (генерация доступна только true).
    is_verified: bool
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


class ForgotPasswordRequest(BaseModel):
    """Тело `POST /auth/forgot-password`."""

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class ResetPasswordRequest(BaseModel):
    """Тело `POST /auth/reset-password`."""

    token: str = Field(min_length=1)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )


class ProfileUpdateRequest(BaseModel):
    """Тело `PATCH /auth/me` (T-082): необязательные поля профиля.

    `None` (явный JSON-``null``) означает «не менять». Запрос без изменений
    (пустое тело или оба поля ``null``) отклоняется 422: иначе клиент получал
    бы «обновлено», хотя ничего не обновилось. Лишние поля запрещены
    (``extra="forbid"``), чтобы опечатка в имени поля не игнорировалась молча.

    ``display_name`` обрезается по краям; пустое после trim имя — 422
    (стереть имя через API нельзя, только заменить на непустое). ``level``
    валидируется enum-ом `Level` (beginner/club/gm), иначе 422.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None, max_length=_DISPLAY_NAME_MAX_LENGTH
    )
    level: Level | None = None

    @field_validator("display_name", mode="before")
    @classmethod
    def _normalize_display_name(cls, value: Any) -> Any:
        """Обрезать пробелы и отклонить пустое имя (до проверки длины)."""
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("display_name не может быть пустым")
            return trimmed
        return value

    @model_validator(mode="after")
    def _require_at_least_one_change(self) -> ProfileUpdateRequest:
        """Хотя бы одно не-``None`` поле — иначе запрос бессмысленен."""
        if self.display_name is None and self.level is None:
            raise ValueError("Укажите display_name и/или level")
        return self


class ChangePasswordRequest(BaseModel):
    """Тело `POST /auth/change-password` (T-082).

    Длины те же, что у регистрации: новый пароль — 8..256 символов.
    Текущий пароль проверяется Argon2-верификатором входа; его длина не
    ограничена снизу (неверный короткий пароль даёт 401, а не 422).
    Новый пароль обязан отличаться от текущего — сравнение до хеширования
    даёт понятную 422 вместо бессмысленной перезаписи тем же значением.
    """

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH
    )

    @model_validator(mode="after")
    def _reject_same_password(self) -> ChangePasswordRequest:
        """Новый пароль должен отличаться от текущего (422)."""
        if self.current_password == self.new_password:
            raise ValueError("Новый пароль должен отличаться от текущего")
        return self


class MessageResponse(BaseModel):
    """Нейтральный ответ (не раскрывает существование email)."""

    detail: str


class EmailVerificationResponse(BaseModel):
    """Результат `GET /auth/verify-email`."""

    email: str
    is_verified: bool


def _to_profile(user: User) -> UserProfile:
    """Преобразовать ORM-пользователя в публичный профиль."""
    return UserProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        level=user.level,
        role=user.role,
        tier=UserTier(user.tier),
        is_active=user.is_active,
        is_verified=bool(user.is_verified),
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
    settings: Annotated[Settings, Depends(get_settings)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> UserProfile:
    """Создать пользователя, отправить письмо подтверждения (201).

    Пользователь создаётся с ``is_verified=False`` (KI-041): `/v1/analyze`
    и `/v1/commentary` вернут 403 `email_not_verified` до перехода по
    ссылке. Ссылка уходит через `MockEmailSender` (structlog) и содержит
    короткоживущий JWT типа ``email_verify``.

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
        is_verified=False,
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
    token = create_email_verification_token(user, settings)
    try:
        await sender.send(
            verification_message(settings, to=user.email, token=token)
        )
    except Exception as exc:  # письмо не должно отменять регистрацию
        logger.warning(
            "email_verification_send_failed",
            user_id=user.id,
            error=str(exc),
        )
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
        # KI-041: logout-all/сброс пароля инвалидируют все refresh,
        # выпущенные до момента отзыва.
        if await is_token_globally_revoked(
            redis_client, claims.user_id, claims.issued_at
        ):
            raise AuthError("Сессия завершена на всех устройствах")
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


def _global_revocation_ttl_s(settings: Settings) -> int:
    """TTL маркера global revocation — не меньше жизни refresh-токена."""
    return settings.refresh_token_ttl_days * 24 * 60 * 60 + 60


@router.get(
    "/verify-email",
    response_model=EmailVerificationResponse,
    summary="Подтвердить email по токену из письма",
)
async def verify_email(
    token: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmailVerificationResponse:
    """Валидировать `email_verify` JWT и выставить `is_verified=True`.

    Повторный переход по ссылке идемпотентен (200, уже подтверждён).

    Raises:
        AuthError: токен битый/просрочен/не того типа или пользователя нет/
            он отключён (→ HTTP 401).
    """
    claims = decode_token(token, settings, expected_type="email_verify")
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthError("Пользователь не найден или отключён")
    if not user.is_verified:
        user.is_verified = True
        await session.commit()
        logger.info("email_verified", user_id=user.id)
    return EmailVerificationResponse(
        email=user.email, is_verified=bool(user.is_verified)
    )


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запросить письмо со ссылкой сброса пароля",
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    sender: Annotated[EmailSender, Depends(get_email_sender)],
) -> MessageResponse:
    """Отправить ссылку сброса, не раскрывая существование email (202).

    Ответ одинаков для зарегистрированного и неизвестного адреса — иначе
    endpoint стал бы оракулом перечисления пользователей. Для неизвестного
    email письмо не отправляется, но ответ тот же.
    """
    user = await session.scalar(
        select(User).where(User.email == payload.email)
    )
    if user is not None:
        token = create_password_reset_token(user, settings)
        try:
            await sender.send(
                password_reset_message(settings, to=user.email, token=token)
            )
        except Exception as exc:  # не раскрываем сбой отправителя клиенту
            logger.warning(
                "password_reset_send_failed",
                user_id=user.id,
                error=str(exc),
            )
        logger.info("password_reset_requested", user_id=user.id)
    else:
        logger.info("password_reset_unknown_email")
    return MessageResponse(
        detail=(
            "Если email зарегистрирован, письмо со ссылкой сброса "
            "отправлено"
        )
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Сбросить пароль по токену из письма",
)
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    """Сменить пароль и глобально отозвать refresh-токены.

    Токен сброса одноразовый: после успешной смены в Redis пишется
    `revoked_before:<user_id>`, и повторное использование того же токена
    отклоняется (его `issued_at` уже не позже маркера). Cookie текущей
    сессии очищается — нужен повторный вход с новым паролем.

    Raises:
        AuthError: токен битый/просрочен/не того типа/уже использован или
            пользователь отключён (→ HTTP 401).
    """
    claims = decode_token(
        payload.token, settings, expected_type="password_reset"
    )
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise AuthError("Пользователь не найден или отключён")
    redis_client = _redis_client(request)
    if (
        redis_client is not None
        and settings.refresh_denylist_enabled
        and await is_token_globally_revoked(
            redis_client, user.id, claims.issued_at
        )
    ):
        raise AuthError("Ссылка сброса уже использована")
    user.password_hash = await asyncio.to_thread(
        hash_password, payload.new_password
    )
    await session.commit()
    if redis_client is not None and settings.refresh_denylist_enabled:
        await revoke_all_refresh_tokens(
            redis_client,
            user.id,
            ttl_s=_global_revocation_ttl_s(settings),
        )
    clear_refresh_cookie(response, settings)
    logger.info("password_reset_completed", user_id=user.id)
    return MessageResponse(
        detail="Пароль обновлён; войдите заново на всех устройствах"
    )


@router.post(
    "/change-password",
    response_model=MessageResponse,
    summary="Сменить пароль, зная текущий",
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    """Сменить пароль аутентифицированного пользователя (T-082).

    Текущий пароль проверяется тем же Argon2-верификатором, что и вход
    (`verify_and_update`, включая legacy bcrypt). После успешной смены
    обновляется `password_hash`, старые refresh-токены глобально отзываются
    тем же механизмом, что у `reset-password` (``revoked_before:<user_id>``),
    а текущая refresh-cookie очищается. Access-токен доживает свой TTL
    (≤ 15 минут) — отдельного denylist для access в проекте нет.

    Raises:
        AuthError: нет/битый access-токен, либо неверный текущий пароль
            (→ HTTP 401, код `unauthorized`).
    """
    password_ok, _ = await asyncio.to_thread(
        verify_and_update, payload.current_password, user.password_hash
    )
    if not password_ok:
        # Тот же код/семантика, что у входа: не раскрываем деталей.
        raise AuthError("Неверный текущий пароль")
    user.password_hash = await asyncio.to_thread(
        hash_password, payload.new_password
    )
    await session.commit()
    redis_client = _redis_client(request)
    revoked = False
    if redis_client is not None and settings.refresh_denylist_enabled:
        revoked = await revoke_all_refresh_tokens(
            redis_client,
            user.id,
            ttl_s=_global_revocation_ttl_s(settings),
        )
    # Своя refresh-cookie тоже отозвана — без очистки клиент хранил бы
    # заведомо нерабочий токен до первого 401.
    clear_refresh_cookie(response, settings)
    logger.info(
        "password_changed", user_id=user.id, refresh_revoked=revoked
    )
    return MessageResponse(
        detail="Пароль обновлён; войдите заново на всех устройствах"
    )


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Выйти со всех устройств (отозвать все refresh-токены)",
)
async def logout_all(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Глобально отозвать refresh-токены пользователя (KI-041).

    В Redis пишется `revoked_before:<user_id>` (Unix-ms); `/auth/refresh`
    отклоняет токены с `issued_at` не позже этого момента. Текущая
    refresh-cookie очищается. Недоступный Redis — fail-open (204 +
    warning), как у обычного logout.

    Raises:
        AuthError: нет/битый access-токен (→ HTTP 401).
    """
    redis_client = _redis_client(request)
    revoked = False
    if redis_client is not None and settings.refresh_denylist_enabled:
        revoked = await revoke_all_refresh_tokens(
            redis_client,
            user.id,
            ttl_s=_global_revocation_ttl_s(settings),
        )
    logger.info("logout_all", user_id=user.id, revoked=revoked)
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


@router.patch(
    "/me",
    response_model=UserProfile,
    summary="Обновить профиль текущего пользователя",
)
async def update_me(
    payload: ProfileUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserProfile:
    """Обновить `display_name` и/или `level` (T-082).

    Присланные поля применяются к пользователю из access-токена и
    сохраняются в сессии. Ответ — тот же профиль, что у `GET /auth/me`,
    поэтому UI не нужен отдельный запрос после сохранения.

    Raises:
        AuthError: нет/битый access-токен (→ HTTP 401).
        pydantic.ValidationError: пустое или длиннее 120 символов имя,
            недопустимый уровень, лишнее поле либо запрос без изменений
            (→ HTTP 422).
    """
    changed: list[str] = []
    if payload.display_name is not None:
        user.display_name = payload.display_name
        changed.append("display_name")
    if payload.level is not None:
        user.level = payload.level
        changed.append("level")
    await session.commit()
    logger.info("profile_updated", user_id=user.id, fields=changed)
    return _to_profile(user)
