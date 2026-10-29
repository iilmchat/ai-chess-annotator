"""Отправка писем: mock-реализация и интерфейс для SMTP (KI-041).

Почему mock первым: в проекте нет SMTP-провайдера и секретов, а сценарии
верификации/сброса пароля должны быть полностью тестируемы. `MockEmailSender`
складывает письма в `outbox` (тесты проверяют ссылку/токен) и пишет
`structlog`-событие с `to`/`subject`/`link` — в dev ссылку видно в логе
uvicorn. Реальный SMTP подключится реализацией того же Protocol-а.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

import structlog
from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.errors import EmailDeliveryError

__all__ = [
    "EmailKind",
    "EmailMessage",
    "EmailSender",
    "MockEmailSender",
    "create_email_sender",
    "get_email_sender",
    "password_reset_message",
    "verification_message",
]

logger = structlog.get_logger(__name__)

EmailKind = Literal["verify_email", "password_reset"]


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Готовое письмо: получатель, тема, текст, токен и ссылка."""

    to: str
    subject: str
    body: str
    kind: EmailKind
    token: str
    link: str


class EmailSender(Protocol):
    """Интерфейс отправки писем (mock/SMTP/провайдер)."""

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо (или залогировать в mock-реализации)."""
        ...


class MockEmailSender:
    """Пишет письмо в structlog и копит `outbox` для тестов."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)
        logger.info(
            "email_mock_sent",
            to=message.to,
            subject=message.subject,
            kind=message.kind,
            link=message.link,
        )


def create_email_sender(settings: Settings) -> EmailSender:
    """Собрать sender по `EMAIL_SENDER` (пока только ``mock``)."""
    name = settings.email_sender.strip().lower()
    if name == "mock":
        return MockEmailSender()
    raise EmailDeliveryError(
        f"EMAIL_SENDER={settings.email_sender!r} не поддерживается "
        "(доступно: mock)"
    )


def get_email_sender(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmailSender:
    """Sender из `app.state` (создаётся в lifespan); fallback — mock."""
    sender = getattr(request.app.state, "email_sender", None)
    if sender is not None:
        return sender  # type: ignore[no-any-return]
    return create_email_sender(settings)


def _link(settings: Settings, path: str, token: str) -> str:
    base = settings.app_base_url.rstrip("/")
    return f"{base}{path}?token={token}"


def verification_message(
    settings: Settings, *, to: str, token: str
) -> EmailMessage:
    """Письмо со ссылкой на frontend-страницу подтверждения email."""
    link = _link(settings, "/verify-email", token)
    return EmailMessage(
        to=to,
        subject="ChessAnnotator: подтверждение email",
        body=(
            "Здравствуйте!\n\n"
            "Подтвердите email, чтобы пользоваться анализом партий:\n"
            f"{link}\n\n"
            f"Ссылка действует {settings.email_verification_ttl_hours} ч."
        ),
        kind="verify_email",
        token=token,
        link=link,
    )


def password_reset_message(
    settings: Settings, *, to: str, token: str
) -> EmailMessage:
    """Письмо со ссылкой на frontend-страницу сброса пароля."""
    link = _link(settings, "/reset-password", token)
    return EmailMessage(
        to=to,
        subject="ChessAnnotator: сброс пароля",
        body=(
            "Здравствуйте!\n\n"
            "Для смены пароля откройте ссылку:\n"
            f"{link}\n\n"
            f"Ссылка действует {settings.password_reset_ttl_min} мин. "
            "Если вы не запрашивали сброс, проигнорируйте письмо."
        ),
        kind="password_reset",
        token=token,
        link=link,
    )
