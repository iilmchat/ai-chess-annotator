"""Отправка писем: mock-реализация, SMTP и интерфейс (KI-041, T-082).

Почему mock первым: в проекте нет SMTP-провайдера и секретов, а сценарии
верификации/сброса пароля должны быть полностью тестируемы. `MockEmailSender`
складывает письма в `outbox` (тесты проверяют ссылку/токен) и пишет
`structlog`-событие с `to`/`subject`/`link` — в dev ссылку видно в логе
uvicorn.

`SMTPEmailSender` (T-082) отправляет те же письма через `aiosmtplib` по
настройкам `SMTP_*`; включается ``EMAIL_SENDER=smtp``. В его логах ссылки
и токена **нет** (секрет), только `kind`/`to`/`subject`.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage as MIMEMessage
from typing import Annotated, Any, Literal, Protocol

import aiosmtplib
import structlog
from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.errors import EmailDeliveryError

__all__ = [
    "EmailKind",
    "EmailMessage",
    "EmailSender",
    "MockEmailSender",
    "SMTPEmailSender",
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


class SMTPEmailSender:
    """Реальная отправка писем через SMTP (aiosmtplib, T-082).

    Письмо собирается как `email.message.EmailMessage` с MIMEText
    (``text/plain``, UTF-8), поэтому кириллица в теме/теле кодируется
    стандартным email-кодеком. Параметры подключения — из `SMTP_*`:
    ``start_tls``/``use_tls`` взаимоисключающие по смыслу (587 vs 465),
    ``username``/``password`` передаются только при заданном `smtp_user`
    (иначе — анонимная отправка).

    В логах — только `kind`/`to`/`subject`: ссылка и токен из письма
    являются секретом (в отличие от mock, где ссылка нужна в dev).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _build_message(self, message: EmailMessage) -> MIMEMessage:
        """Собрать MIME-письмо: From/To/Subject + text/plain UTF-8."""
        mime = MIMEMessage()
        mime["From"] = self._settings.smtp_from
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body, subtype="plain", charset="utf-8")
        return mime

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо; ошибка транспорта → `EmailDeliveryError`."""
        settings = self._settings
        mime = self._build_message(message)
        send_kwargs: dict[str, Any] = {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
            "start_tls": settings.smtp_starttls,
            "use_tls": settings.smtp_ssl,
            "timeout": settings.smtp_timeout,
        }
        if settings.smtp_user:
            send_kwargs["username"] = settings.smtp_user
            send_kwargs["password"] = settings.smtp_password
        try:
            await aiosmtplib.send(mime, **send_kwargs)
        except Exception as exc:
            logger.warning(
                "email_smtp_failed",
                to=message.to,
                kind=message.kind,
                error=str(exc),
            )
            raise EmailDeliveryError(
                f"Не удалось отправить письмо на {message.to}: {exc}"
            ) from exc
        logger.info(
            "email_smtp_sent",
            to=message.to,
            subject=message.subject,
            kind=message.kind,
        )


def create_email_sender(settings: Settings) -> EmailSender:
    """Собрать sender по `EMAIL_SENDER`: ``mock`` или ``smtp`` (T-082).

    Raises:
        EmailDeliveryError: ``smtp`` без `SMTP_HOST`/`SMTP_FROM` или
            неизвестный backend (→ 503 на уровне API, ошибка на старте
            приложения).
    """
    name = settings.email_sender.strip().lower()
    if name == "mock":
        return MockEmailSender()
    if name == "smtp":
        if not settings.smtp_host.strip() or not settings.smtp_from.strip():
            raise EmailDeliveryError(
                "EMAIL_SENDER=smtp требует SMTP_HOST и SMTP_FROM"
            )
        return SMTPEmailSender(settings)
    raise EmailDeliveryError(
        f"EMAIL_SENDER={settings.email_sender!r} не поддерживается "
        "(доступно: mock, smtp)"
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
