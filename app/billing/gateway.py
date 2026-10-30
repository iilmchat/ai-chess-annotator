"""Stripe-шлюз: Customer, Checkout, Customer Portal и проверка webhook (T-083).

Слой отделён от FastAPI-эндпоинтов протоколом :class:`BillingGateway`, чтобы
тесты могли подменить сетевые вызовы Stripe, не поднимая HTTP-моки. Реальная
реализация — :class:`StripeBilling` поверх официального SDK.

Почему все вызовы Stripe уходят в `asyncio.to_thread`: SDK синхронный, а
эндпоинты — `async`. Блокировать event loop на время сетевого запроса к
Stripe нельзя: это задержало бы все остальные запросы воркера.

Секреты (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`) никогда не логируются
и не попадают в ответы API.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Annotated, Any, Protocol, cast

import stripe
import structlog
from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.errors import (
    BillingError,
    BillingNotConfiguredError,
    BillingSignatureError,
)

__all__ = [
    "BillingGateway",
    "StripeBilling",
    "create_billing",
    "get_billing",
]

logger = structlog.get_logger(__name__)


class BillingGateway(Protocol):
    """Интерфейс биллинга (Stripe и тестовые реализации)."""

    @property
    def configured(self) -> bool:
        """Заданы ли ключ и цена — можно ли открывать Checkout/Portal."""
        ...

    @property
    def webhook_configured(self) -> bool:
        """Задан ли секрет — можно ли проверять подпись webhook."""
        ...

    async def find_or_create_customer(
        self,
        *,
        user_id: int,
        email: str,
        existing_customer_id: str | None = None,
    ) -> str:
        """Вернуть Stripe Customer id, создав клиента при необходимости."""
        ...

    async def create_checkout_session(
        self,
        *,
        user_id: int,
        customer_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """Создать Checkout Session на подписку и вернуть URL оплаты."""
        ...

    async def create_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> str:
        """Создать сессию Stripe Customer Portal и вернуть её URL."""
        ...

    def parse_webhook(
        self, payload: bytes, signature: str
    ) -> Mapping[str, Any]:
        """Проверить подпись webhook и вернуть событие."""
        ...


class StripeBilling:
    """Реальный Stripe-шлюз (SDK синхронный, вызовы — в thread pool)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # SDK читает ключ из модульной переменной. Один процесс — один
        # Stripe-аккаунт, поэтому глобальная настройка безопасна; для
        # параллельных аккаунтов понадобился бы StripeClient на запрос.
        if settings.stripe_secret_key.strip():
            stripe.api_key = settings.stripe_secret_key.strip()

    @property
    def configured(self) -> bool:
        return self._settings.billing_configured

    @property
    def webhook_configured(self) -> bool:
        return self._settings.billing_webhook_configured

    async def find_or_create_customer(
        self,
        *,
        user_id: int,
        email: str,
        existing_customer_id: str | None = None,
    ) -> str:
        """Найти клиента по email или создать нового.

        Поиск по email закрывает окно «Customer создан, но id не успел
        сохраниться в БД»: повторный checkout не наплодит дублей. Если id
        уже есть в `users.stripe_customer_id` — используем его без запроса.
        """
        if existing_customer_id:
            return existing_customer_id
        if not self.configured:
            raise BillingNotConfiguredError(
                "Stripe не настроен: задайте STRIPE_SECRET_KEY и STRIPE_PRICE_ID"
            )
        try:
            return await asyncio.to_thread(
                self._find_or_create_customer_sync, user_id, email
            )
        except BillingError:
            raise
        except Exception as exc:  # сеть/ошибка Stripe — не 500 без контекста
            logger.warning(
                "billing_customer_failed", user_id=user_id, error=str(exc)
            )
            raise BillingError(
                "Не удалось создать клиента Stripe; попробуйте позже"
            ) from exc

    def _find_or_create_customer_sync(self, user_id: int, email: str) -> str:
        """Синхронная часть поиска/создания Customer (в thread pool)."""
        customers = stripe.Customer.list(email=email, limit=1)
        if customers.data:
            return str(customers.data[0].id)
        customer = stripe.Customer.create(
            email=email, metadata={"user_id": str(user_id)}
        )
        return str(customer.id)

    async def create_checkout_session(
        self,
        *,
        user_id: int,
        customer_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """Checkout Session на подписку Pro (`STRIPE_PRICE_ID`)."""
        if not self.configured:
            raise BillingNotConfiguredError(
                "Stripe не настроен: задайте STRIPE_SECRET_KEY и STRIPE_PRICE_ID"
            )
        metadata = {"user_id": str(user_id)}
        try:
            session = await asyncio.to_thread(
                stripe.checkout.Session.create,
                mode="subscription",
                customer=customer_id,
                line_items=[
                    {"price": self._settings.stripe_price_id, "quantity": 1}
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=str(user_id),
                metadata=metadata,
                subscription_data={"metadata": metadata},
                allow_promotion_codes=True,
            )
        except Exception as exc:
            logger.warning(
                "billing_checkout_failed", user_id=user_id, error=str(exc)
            )
            raise BillingError(
                "Не удалось создать сессию оплаты; попробуйте позже"
            ) from exc
        url = getattr(session, "url", None)
        if not url:
            logger.warning("billing_checkout_without_url", user_id=user_id)
            raise BillingError("Stripe не вернул URL оплаты")
        return str(url)

    async def create_portal_session(
        self, *, customer_id: str, return_url: str
    ) -> str:
        """Сессия Customer Portal: управление оплатой и отмена подписки."""
        if not self.configured:
            raise BillingNotConfiguredError(
                "Stripe не настроен: задайте STRIPE_SECRET_KEY и STRIPE_PRICE_ID"
            )
        try:
            session = await asyncio.to_thread(
                stripe.billing_portal.Session.create,
                customer=customer_id,
                return_url=return_url,
            )
        except Exception as exc:
            logger.warning(
                "billing_portal_failed",
                customer_id=customer_id,
                error=str(exc),
            )
            raise BillingError(
                "Не удалось открыть портал управления подпиской"
            ) from exc
        url = getattr(session, "url", None)
        if not url:
            logger.warning("billing_portal_without_url", customer_id=customer_id)
            raise BillingError("Stripe не вернул URL портала")
        return str(url)

    def parse_webhook(
        self, payload: bytes, signature: str
    ) -> Mapping[str, Any]:
        """Проверить `Stripe-Signature` и разобрать событие.

        Raises:
            BillingNotConfiguredError: не задан `STRIPE_WEBHOOK_SECRET`.
            BillingSignatureError: подпись отсутствует/неверна/просрочена.
        """
        secret = self._settings.stripe_webhook_secret.strip()
        if not secret:
            raise BillingNotConfiguredError(
                "Stripe webhook не настроен: задайте STRIPE_WEBHOOK_SECRET"
            )
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, secret
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            logger.warning("billing_webhook_signature_invalid")
            raise BillingSignatureError(
                "Неверная подпись Stripe webhook"
            ) from exc
        return cast("Mapping[str, Any]", event)


def create_billing(settings: Settings) -> BillingGateway:
    """Собрать шлюз из настроек (Stripe; без ключа эндпоинты вернут 503)."""
    return StripeBilling(settings)


def get_billing(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> BillingGateway:
    """Шлюз из `app.state` (создаётся в lifespan); fallback — новый Stripe."""
    billing = getattr(request.app.state, "billing", None)
    if billing is not None:
        return cast("BillingGateway", billing)
    return create_billing(settings)
