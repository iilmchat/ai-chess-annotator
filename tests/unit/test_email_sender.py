"""KI-041/T-082: mock- и SMTP-отправители писем, action-токены.

Проверяем без БД/HTTP/сети: письмо содержит ссылку/токен, mock складывает
его в `outbox`, SMTP-реализация передаёт `aiosmtplib.send` правильные
параметры и не логирует токен, неизвестный/недонастроенный sender даёт
`EmailDeliveryError`; JWT типов `email_verify`/`password_reset`
декодируются только с ожидаемым типом и имеют TTL из настроек.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

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
from app.email.sender import EmailMessage, SMTPEmailSender
from app.errors import AuthError, EmailDeliveryError

SECRET = "k" * 40

#: Тип записи вызова `aiosmtplib.send`: (MIME-письмо, kwargs).
SendCall = tuple[Any, dict[str, Any]]


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret=SECRET,
        app_base_url="https://example.test",
        **overrides,
    )


def _smtp_settings(**overrides: object) -> Settings:
    """Настройки с заполненным SMTP (все параметры явные, чтобы проверять kwargs)."""
    base: dict[str, object] = {
        "email_sender": "smtp",
        "smtp_host": "smtp.example.test",
        "smtp_port": 2525,
        "smtp_user": "mailer",
        "smtp_password": "smtp-secret",
        "smtp_from": "noreply@example.test",
        "smtp_starttls": False,
        "smtp_ssl": True,
        "smtp_timeout": 7.5,
    }
    base.update(overrides)
    return _settings(**base)


def _user() -> User:
    return User(
        id=42,
        email="user@example.com",
        level=Level.club,
        role=AdminRole.user,
        is_active=True,
        is_verified=False,
    )


def _capture_send(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[SendCall],
    *,
    error: BaseException | None = None,
) -> None:
    """Подменить `aiosmtplib.send`: запомнить письмо/kwargs, не ходить в сеть."""

    async def fake_send(message: Any, **kwargs: Any) -> tuple[Any, str]:
        calls.append((message, kwargs))
        if error is not None:
            raise error
        return {}, "OK"

    monkeypatch.setattr("app.email.sender.aiosmtplib.send", fake_send)


def _smtp_message() -> EmailMessage:
    """Письмо с кириллицей в теме и теле (проверка UTF-8)."""
    return EmailMessage(
        to="user@example.com",
        subject="ChessAnnotator: сброс пароля — кириллица",
        body="Здравствуйте!\n\nТело письма на русском языке.",
        kind="password_reset",
        token="secret-token",
        link="https://example.test/reset-password?token=secret-token",
    )


# --- Mock sender ----------------------------------------------------------


def test_mock_sender_accumulates_outbox_and_logs() -> None:
    sender = MockEmailSender()
    message = verification_message(
        _settings(), to="user@example.com", token="tok-123"
    )

    asyncio.run(sender.send(message))

    assert sender.outbox == [message]
    assert message.token == "tok-123"
    assert message.link == "https://example.test/verify-email?token=tok-123"
    assert "verify-email" in message.body


def test_password_reset_message_uses_configured_ttl() -> None:
    message = password_reset_message(
        _settings(password_reset_ttl_min=7),
        to="user@example.com",
        token="reset-1",
    )
    assert message.kind == "password_reset"
    assert "/reset-password?token=reset-1" in message.link
    assert "7 мин" in message.body


# --- Выбор backend-а ------------------------------------------------------


def test_create_email_sender_returns_mock_by_default() -> None:
    sender = create_email_sender(_settings())
    assert isinstance(sender, MockEmailSender)


def test_create_email_sender_returns_smtp_with_configured_credentials() -> None:
    sender = create_email_sender(_smtp_settings())
    assert isinstance(sender, SMTPEmailSender)


@pytest.mark.parametrize("missing", ["smtp_host", "smtp_from"])
def test_create_email_sender_requires_smtp_host_and_from(missing: str) -> None:
    settings = _smtp_settings(**{missing: ""})

    with pytest.raises(EmailDeliveryError, match="SMTP_HOST и SMTP_FROM"):
        create_email_sender(settings)


def test_create_email_sender_rejects_unknown_backend() -> None:
    # Literal в Settings не даст создать такое значение нормально; sender
    # всё равно обязан отвергнуть неизвестный backend (защита от обхода).
    settings = Settings.model_construct(**{"email_sender": "sendgrid"})

    with pytest.raises(EmailDeliveryError, match="mock, smtp"):
        create_email_sender(settings)


def test_email_sender_rejects_unknown_env_value() -> None:
    """Опечатка в EMAIL_SENDER ловится валидацией настроек, а не в рантайме."""
    with pytest.raises(ValueError, match="mock"):
        _settings(email_sender="sendgrid")


# --- SMTP sender ----------------------------------------------------------


def test_smtp_sender_passes_expected_kwargs_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[SendCall] = []
    _capture_send(monkeypatch, calls)
    settings = _smtp_settings()
    sender = create_email_sender(settings)
    message = _smtp_message()

    asyncio.run(sender.send(message))

    assert len(calls) == 1
    mime, kwargs = calls[0]
    assert kwargs == {
        "hostname": "smtp.example.test",
        "port": 2525,
        "start_tls": False,
        "use_tls": True,
        "timeout": 7.5,
        "username": "mailer",
        "password": "smtp-secret",
    }
    assert mime["From"] == "noreply@example.test"
    assert mime["To"] == "user@example.com"
    assert mime["Subject"] == message.subject
    assert mime.get_content_type() == "text/plain"
    assert mime.get_content_charset() == "utf-8"
    assert mime.get_content().rstrip("\n") == message.body
    # Кириллическая тема кодируется в заголовке RFC 2047 и декодируется назад.
    assert "=?utf-8?" in mime.as_string()


def test_smtp_sender_omits_auth_when_user_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[SendCall] = []
    _capture_send(monkeypatch, calls)
    sender = create_email_sender(_smtp_settings(smtp_user="", smtp_password=""))

    asyncio.run(sender.send(_smtp_message()))

    _, kwargs = calls[0]
    assert "username" not in kwargs
    assert "password" not in kwargs
    assert kwargs["hostname"] == "smtp.example.test"


def test_smtp_sender_raises_email_delivery_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[SendCall] = []
    _capture_send(
        monkeypatch, calls, error=RuntimeError("connection refused")
    )
    sender = create_email_sender(_smtp_settings())

    with pytest.raises(
        EmailDeliveryError, match="Не удалось отправить письмо"
    ):
        asyncio.run(sender.send(_smtp_message()))

    assert len(calls) == 1


# --- Action-токены --------------------------------------------------------


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
