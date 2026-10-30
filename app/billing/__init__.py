"""Биллинг ChessAnnotator: Stripe Checkout, webhook и Customer Portal (T-083).

Пакет намеренно не импортирует FastAPI-роутер: сетевой слой — в
`app.api.v1.billing`, работа с БД по событиям — в `service.py`, SDK Stripe —
в `gateway.py`. Так обработчик webhook можно тестировать без HTTP.
"""

from app.billing.gateway import (
    BillingGateway,
    StripeBilling,
    create_billing,
    get_billing,
)
from app.billing.service import apply_webhook_event

__all__ = [
    "BillingGateway",
    "StripeBilling",
    "apply_webhook_event",
    "create_billing",
    "get_billing",
]
