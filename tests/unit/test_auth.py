"""Юнит-тесты фундамента аутентификации (T-028).

Проверяем то, что не требует БД и HTTP:

* bcrypt-хеширование паролей (:mod:`app.auth.passwords`);
* JWT access/refresh: полезная нагрузка, тип, срок, подпись
  (:mod:`app.auth.tokens`);
* схемы запросов `/auth` (Pydantic v2, нормализация email, лимиты пароля).

Интеграционные сценарии эндпоинтов — в
`tests/integration/test_auth_api.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api.v1.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    register,
)
from app.auth.passwords import MAX_PASSWORD_BYTES, hash_password, verify_password
from app.auth.tokens import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.config import Settings
from app.db.models import AdminRole, Level, User
from app.errors import AuthError, UserExistsError

PASSWORD = "correct horse battery staple"
GOOD_EMAIL = "user@example.com"


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


# --- Пароли ---------------------------------------------------------------


def test_hash_password_roundtrip() -> None:
    password_hash = hash_password(PASSWORD)

    assert password_hash != PASSWORD
    assert password_hash.startswith("$2")
    assert verify_password(PASSWORD, password_hash) is True


def test_verify_password_rejects_wrong_password() -> None:
    password_hash = hash_password(PASSWORD)

    assert verify_password("not-the-password", password_hash) is False


def test_verify_password_rejects_missing_or_broken_hash() -> None:
    password_hash = hash_password(PASSWORD)

    assert verify_password(PASSWORD, None) is False
    assert verify_password(PASSWORD, "") is False
    assert verify_password(PASSWORD, "not-a-bcrypt-hash") is False
    assert verify_password("", password_hash) is False


def test_hash_password_rejects_over_72_bytes() -> None:
    # "я" — 2 байта UTF-8: 40 символов = 80 байт, bcrypt такое обрезал бы.
    with pytest.raises(ValueError):
        hash_password("я" * 40)


def test_hash_password_accepts_exactly_72_bytes() -> None:
    password = "я" * (MAX_PASSWORD_BYTES // 2)  # 36 символов, ровно 72 байта
    password_hash = hash_password(password)

    assert verify_password(password, password_hash) is True


def test_hash_password_rejects_empty_password() -> None:
    with pytest.raises(ValueError):
        hash_password("")


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


@pytest.mark.parametrize("password", ["short", "a" * 73, "я" * 40])
def test_credentials_reject_bad_password_length(password: str) -> None:
    # "short" — короче 8 символов, "a"*73 — длиннее 72 символов,
    # "я"*40 — 40 символов, но 80 байт UTF-8 (лимит bcrypt).
    with pytest.raises(ValidationError):
        RegisterRequest(email=GOOD_EMAIL, password=password)


def test_credentials_accept_password_boundaries() -> None:
    assert RegisterRequest(email=GOOD_EMAIL, password="a" * 8).password == "a" * 8
    assert (
        RegisterRequest(email=GOOD_EMAIL, password="a" * 72).password == "a" * 72
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

