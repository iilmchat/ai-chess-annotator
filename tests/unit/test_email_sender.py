"""KI-041: mock-отправитель писем и action-токены.

Проверяем без БД/HTTP: письмо содержит ссылку/токен, mock складывает его
в `outbox`, неизвестный sender даёт `EmailDeliveryError`; JWT типов
`email_verify`/`password_reset` декодируются только с ожидаемым типом и
имеют TTL из настроек.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.auth.tokens import (
    create_email_verification_token,
    create_password_reset_token,
    decode_token,
)
from app.config import Settings
from app.db.models import AdminRole, Level, User
from app.email import (
    MockEmailSender,
    create_email_sender,
    password_reset_message,
    verification_message,
)
from app.errors import AuthError, EmailDeliveryError

SECRET = "k" * 40


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret=SECRET,
        app_base_url="https://example.test",
        **overrides,
    )


def _user() -> User:
    return User(
        id=42,
        email="user@example.com",
        level=Level.club,
        role=AdminRole.user,
        is_active=True,
        is_verified=False,
    )


def test_mock_sender_accumulates_outbox_and_logs() -> None:
    sender = MockEmailSender()
    message = verification_message(
        _settings(), to="user@example.com", token="tok-123"
    )

    asyncio.run(sender.send(message))

    assert sender.outbox == [message]
    assert message.token == "tok-123"
    assert message.link == (
        "https://example.test/auth/verify-email?token=tok-123"
    )
    assert "verify-email" in message.body


def test_password_reset_message_uses_configured_ttl() -> None:
    message = password_reset_message(
        _settings(password_reset_ttl_min=7),
        to="user@example.com",
        token="reset-1",
    )
    assert message.kind == "password_reset"
    assert "/auth/reset-password?token=reset-1" in message.link
    assert "7 мин" in message.body


def test_create_email_sender_rejects_unknown_backend() -> None:
    with pytest.raises(EmailDeliveryError, match="не поддерживается"):
        create_email_sender(_settings(email_sender="smtp"))


def test_create_email_sender_returns_mock_by_default() -> None:
    sender = create_email_sender(_settings())
    assert isinstance(sender, MockEmailSender)


def test_verification_token_has_expected_type_and_ttl() -> None:
    settings = _settings(email_verification_ttl_hours=2)
    token = create_email_verification_token(_user(), settings)
    payload = decode_token(token, settings, expected_type="email_verify")
    assert payload.user_id == 42
    assert payload.token_type == "email_verify"
    assert payload.issued_at is not None
    assert payload.expires_at > datetime.now(UTC) + timedelta(hours=1)

    with pytest.raises(AuthError):
        decode_token(token, settings, expected_type="access")


def test_password_reset_token_has_expected_type_and_ttl() -> None:
    settings = _settings(password_reset_ttl_min=5)
    token = create_password_reset_token(_user(), settings)
    payload = decode_token(token, settings, expected_type="password_reset")
    assert payload.token_type == "password_reset"
    assert payload.expires_at > datetime.now(UTC) + timedelta(minutes=1)
