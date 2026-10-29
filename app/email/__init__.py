"""Email-инфраструктура (KI-041).

Сейчас используется :class:`MockEmailSender`: письмо не уходит по SMTP, а
пишется в structlog (`email_mock_sent` с ссылкой). Этого достаточно для
dev/CI и для сценария «пользователь открывает ссылку из лога». Интерфейс
`EmailSender` отделён от реализации, чтобы позже подключить SMTP/провайдера
без изменений в auth-эндпоинтах.
"""

from app.email.sender import (
    EmailMessage,
    EmailSender,
    MockEmailSender,
    create_email_sender,
    get_email_sender,
    password_reset_message,
    verification_message,
)

__all__ = [
    "EmailMessage",
    "EmailSender",
    "MockEmailSender",
    "create_email_sender",
    "get_email_sender",
    "password_reset_message",
    "verification_message",
]
