"""Юнит-тесты фундамента аутентификации (T-028, KI-036/KI-037).

Проверяем то, что не требует БД и HTTP:

* Argon2id-хеширование и миграцию legacy bcrypt → Argon2id
  (:mod:`app.auth.passwords`);
* JWT access/refresh: полезная нагрузка, тип, срок, подпись
  (:mod:`app.auth.tokens`);
* схемы запросов `/auth` (Pydantic v2, нормализация email, лимиты пароля);
* хелперы HttpOnly-cookie (:mod:`app.auth.cookies`);
* хелперы Redis-denylist (:mod:`app.auth.denylist`) с `AsyncMock` вместо Redis.

Интеграционные сценарии эндпоинтов — в
`tests/integration/test_auth_api.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from app.api.v1.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    register,
)
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
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
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
from app.config import Settings
from app.db.models import AdminRole, Level, User
from app.errors import AuthError, UserExistsError

PASSWORD = "correct horse battery staple"
GOOD_EMAIL = "user@example.com"

#: Готовый bcrypt-хеш `PASSWORD` (cost 12), как в БД итерации 24.
#: Легаси-строка зафиксирована, чтобы тест не зависел от bcrypt-API генерации.
LEGACY_BCRYPT_HASH = (
    "$2b$12$FjSoHD1FbK4YiEh629IIo.Ithyg6QrQOUvvY5z41G6Fjdw.Dfd29C"
)


def _settings(**overrides: int) -> Settings:
    """Настройки для юнит-тестов: фиксированный секрет и TTL."""
    return Settings(
        _env_file=None,
        jwt_secret="unit-test-secret-0123456789",
        access_token_ttl_min=overrides.get("access_token_ttl_min", 15),
        refresh_token_ttl_days=overrides.get("refresh_token_ttl_days", 7),
    )


def _user(role: AdminRole = AdminRole.user, user_id: int = 42) -> User:
    """Несохранённый ORM-пользователь (для подписи токенов хватает id/role)."""
    return User(
        id=user_id,
        email=GOOD_EMAIL,
        level=Level.club,
        role=role,
        is_active=True,
    )


# --- Пароли: Argon2id -----------------------------------------------------


def test_hash_password_roundtrip_uses_argon2id() -> None:
    password_hash = hash_password(PASSWORD)

    assert password_hash != PASSWORD
    assert password_hash.startswith("$argon2id$")
    assert verify_password(PASSWORD, password_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = hash_password(PASSWORD)

    assert verify_password("not-the-password", password_hash) is False


def test_verify_password_rejects_missing_or_broken_hash() -> None:
    password_hash = hash_password(PASSWORD)

    assert verify_password(PASSWORD, None) is False
    assert verify_password(PASSWORD, "") is False
    assert verify_password(PASSWORD, "not-a-hash") is False
    assert verify_password(PASSWORD, "$argon2id$broken") is False
    assert verify_password("", password_hash) is False


def test_hash_password_accepts_more_than_72_bytes() -> None:
    # bcrypt-лимита 72 байта больше нет: Argon2id принимает длинный вход.
    password = "я" * MAX_PASSWORD_LENGTH  # 256 символов = 512 байт UTF-8
    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert verify_password(password, password_hash) is True


def test_hash_password_rejects_too_long_password() -> None:
    with pytest.raises(ValueError):
        hash_password("a" * (MAX_PASSWORD_LENGTH + 1))


def test_hash_password_rejects_empty_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")


# --- Пароли: legacy bcrypt и миграция -------------------------------------


def test_legacy_bcrypt_hash_still_verifies() -> None:
    assert verify_password(PASSWORD, LEGACY_BCRYPT_HASH) is True
    assert verify_password("wrong-password", LEGACY_BCRYPT_HASH) is False


def test_verify_and_update_migrates_bcrypt_to_argon2id() -> None:
    matched, upgraded = verify_and_update(PASSWORD, LEGACY_BCRYPT_HASH)

    assert matched is True
    assert upgraded is not None
    assert upgraded.startswith("$argon2id$")
    assert verify_password(PASSWORD, upgraded) is True


def test_verify_and_update_does_not_rehash_current_argon2() -> None:
    password_hash = hash_password(PASSWORD)

    matched, upgraded = verify_and_update(PASSWORD, password_hash)

    assert matched is True
    assert upgraded is None


def test_verify_and_update_rejects_wrong_password() -> None:
    assert verify_and_update("wrong", hash_password(PASSWORD)) == (False, None)
    assert verify_and_update("wrong", LEGACY_BCRYPT_HASH) == (False, None)


def test_verify_and_update_handles_broken_or_empty_hash() -> None:
    assert verify_and_update(PASSWORD, None) == (False, None)
    assert verify_and_update(PASSWORD, "") == (False, None)
    assert verify_and_update(PASSWORD, "not-a-hash") == (False, None)
    assert verify_and_update("", hash_password(PASSWORD)) == (False, None)


# --- Cookie ---------------------------------------------------------------


def _cookie_header(response: Response) -> str:
    return response.headers["set-cookie"]


def test_set_refresh_cookie_has_security_flags() -> None:
    settings = _settings()
    response = Response()

    set_refresh_cookie(response, "refresh.jwt.value", settings)

    header = _cookie_header(response)
    assert "refresh_token=refresh.jwt.value" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/auth" in header
    assert f"Max-Age={7 * 24 * 60 * 60}" in header
    assert "Secure" not in header
    assert "Domain=" not in header


def test_set_refresh_cookie_secure_and_domain_overrides() -> None:
    settings = Settings(
        _env_file=None,
        refresh_token_ttl_days=1,
        auth_cookie_secure=True,
        auth_cookie_samesite="strict",
        auth_cookie_domain="example.com",
    )
    response = Response()

    set_refresh_cookie(response, "token-value", settings)

    header = _cookie_header(response)
    assert "Secure" in header
    assert "SameSite=strict" in header
    assert "Domain=example.com" in header
    assert f"Max-Age={24 * 60 * 60}" in header


def test_clear_refresh_cookie_expires_it() -> None:
    settings = _settings()
    response = Response()

    clear_refresh_cookie(response, settings)

    header = _cookie_header(response)
    assert "refresh_token=" in header
    assert "Max-Age=0" in header
    assert "Path=/auth" in header
    assert "HttpOnly" in header


def test_refresh_cookie_max_age_matches_ttl() -> None:
    assert refresh_cookie_max_age_s(_settings()) == 7 * 24 * 60 * 60


def test_samesite_none_requires_secure_cookie() -> None:
    """LOW-1: SameSite=None без Secure браузеры отвергают — ошибка конфига."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            auth_cookie_samesite="none",
            auth_cookie_secure=False,
        )

    # С Secure=true такой конфиг допустим (prod за HTTPS).
    settings = Settings(
        _env_file=None,
        auth_cookie_samesite="none",
        auth_cookie_secure=True,
    )
    assert settings.auth_cookie_samesite == "none"


# --- Denylist -------------------------------------------------------------


def _refresh_payload(*, jti: str = "abc123") -> TokenPayload:
    return TokenPayload(
        user_id=42,
        role="user",
        token_type="refresh",
        jti=jti,
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )


def test_denylist_key_prefix() -> None:
    assert denylist_key("abc123") == "refresh_denylist:abc123"


def test_revoke_refresh_token_sets_key_with_ttl_to_exp() -> None:
    client = AsyncMock()

    revoked = asyncio.run(revoke_refresh_token(client, _refresh_payload()))

    assert revoked is True
    client.setex.assert_awaited_once()
    key, ttl, value = client.setex.await_args.args
    assert key == "refresh_denylist:abc123"
    assert value == "1"
    assert 0 < ttl <= 120


def test_revoke_expired_token_does_not_write() -> None:
    client = AsyncMock()
    payload = TokenPayload(
        user_id=42,
        role="user",
        token_type="refresh",
        jti="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    revoked = asyncio.run(revoke_refresh_token(client, payload))

    assert revoked is False
    client.setex.assert_not_awaited()


def test_revoke_is_fail_open_when_redis_unavailable() -> None:
    client = AsyncMock()
    client.setex.side_effect = RuntimeError("redis down")

    assert asyncio.run(revoke_refresh_token(client, _refresh_payload())) is False


def test_is_refresh_token_revoked_reads_key() -> None:
    client = AsyncMock()
    client.exists.return_value = 1

    assert asyncio.run(is_refresh_token_revoked(client, "abc123")) is True
    client.exists.assert_awaited_once_with("refresh_denylist:abc123")


def test_is_refresh_token_not_revoked_returns_false() -> None:
    client = AsyncMock()
    client.exists.return_value = 0

    assert asyncio.run(is_refresh_token_revoked(client, "abc123")) is False


def test_denylist_check_is_fail_open_when_redis_unavailable() -> None:
    client = AsyncMock()
    client.exists.side_effect = RuntimeError("redis down")

    assert asyncio.run(is_refresh_token_revoked(client, "abc123")) is False


# --- Токены ---------------------------------------------------------------


def test_access_token_roundtrip() -> None:
    settings = _settings()

    token = create_access_token(_user(AdminRole.admin), settings)
    payload = decode_token(token, settings, expected_type="access")

    assert payload.user_id == 42
    assert payload.role == "admin"
    assert payload.token_type == "access"
    assert payload.jti
    assert payload.expires_at > datetime.now(UTC)
    assert payload.expires_at <= datetime.now(UTC) + timedelta(minutes=16)


def test_refresh_token_roundtrip() -> None:
    settings = _settings()

    token = create_refresh_token(_user(), settings)
    payload = decode_token(token, settings, expected_type="refresh")

    assert payload.user_id == 42
    assert payload.token_type == "refresh"
    assert payload.expires_at > datetime.now(UTC) + timedelta(days=6)


def test_access_token_rejected_as_refresh() -> None:
    settings = _settings()
    token = create_access_token(_user(), settings)

    with pytest.raises(AuthError):
        decode_token(token, settings, expected_type="refresh")


def test_refresh_token_rejected_as_access() -> None:
    settings = _settings()
    token = create_refresh_token(_user(), settings)

    with pytest.raises(AuthError):
        decode_token(token, settings, expected_type="access")


def test_tokens_get_unique_jti() -> None:
    settings = _settings()
    user = _user()

    first = decode_token(
        create_access_token(user, settings), settings, expected_type="access"
    )
    second = decode_token(
        create_access_token(user, settings), settings, expected_type="access"
    )

    assert first.jti != second.jti


def test_tampered_token_rejected() -> None:
    settings = _settings()
    token = create_access_token(_user(), settings)
    header, claims, signature = token.split(".")
    tampered = f"{header}.{claims}.{signature[:-4]}AAAA"

    with pytest.raises(AuthError):
        decode_token(tampered, settings)


def test_expired_token_rejected() -> None:
    settings = _settings(access_token_ttl_min=-1)

    token = create_access_token(_user(), settings)

    with pytest.raises(AuthError):
        decode_token(token, settings, expected_type="access")


def test_token_with_wrong_secret_rejected() -> None:
    issuer = _settings()
    verifier = Settings(
        _env_file=None,
        jwt_secret="another-secret-9876543210",
        access_token_ttl_min=15,
        refresh_token_ttl_days=7,
    )
    token = create_access_token(_user(), issuer)

    with pytest.raises(AuthError):
        decode_token(token, verifier, expected_type="access")


def test_malformed_token_rejected() -> None:
    settings = _settings()

    with pytest.raises(AuthError):
        decode_token("this-is-not-a-jwt", settings)


# --- Схемы запросов -------------------------------------------------------


def test_register_request_normalizes_email_to_lowercase() -> None:
    request = RegisterRequest(email=" User@Example.COM ", password="12345678")

    assert request.email == "user@example.com"


def test_login_request_accepts_valid_credentials() -> None:
    request = LoginRequest(email=GOOD_EMAIL, password=PASSWORD)

    assert request.email == GOOD_EMAIL
    assert request.password == PASSWORD


def test_credentials_reject_invalid_email() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="not-an-email", password=PASSWORD)


@pytest.mark.parametrize(
    "password",
    ["short", "a" * (MAX_PASSWORD_LENGTH + 1)],
)
def test_credentials_reject_bad_password_length(password: str) -> None:
    # "short" — короче 8 символов, "a"*257 — длиннее 256 символов.
    with pytest.raises(ValidationError):
        RegisterRequest(email=GOOD_EMAIL, password=password)


def test_credentials_accept_password_boundaries() -> None:
    assert RegisterRequest(email=GOOD_EMAIL, password="a" * 8).password == "a" * 8
    long_password = "a" * MAX_PASSWORD_LENGTH
    assert (
        RegisterRequest(email=GOOD_EMAIL, password=long_password).password
        == long_password
    )
    # Argon2id не ограничен 72 байтами: 256 кириллических символов допустимы.
    multibyte = "я" * MAX_PASSWORD_LENGTH
    assert (
        RegisterRequest(email=GOOD_EMAIL, password=multibyte).password
        == multibyte
    )


def test_refresh_request_requires_token() -> None:
    with pytest.raises(ValidationError):
        RefreshRequest(refresh_token="")


# --- Регистрация: гонка уникального email ---------------------------------


class _RacingSession:
    """Заглушка AsyncSession: pre-check пуст, commit падает на уникальном индексе."""

    def __init__(self) -> None:
        self.rolled_back = False
        self.added: list[Any] = []

    async def scalar(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        raise IntegrityError(
            "INSERT INTO users", {}, Exception("duplicate key value")
        )

    async def rollback(self) -> None:
        self.rolled_back = True


def test_register_maps_unique_race_to_email_taken() -> None:
    session = _RacingSession()
    request = RegisterRequest(email=GOOD_EMAIL, password=PASSWORD)

    with pytest.raises(UserExistsError) as excinfo:
        asyncio.run(register(request, session))  # type: ignore[arg-type]

    assert excinfo.value.code == "email_taken"
    assert session.rolled_back is True
    assert len(session.added) == 1
