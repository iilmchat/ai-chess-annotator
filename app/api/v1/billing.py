"""`/v1/billing` — Stripe Checkout, Customer Portal и webhook (T-083).

Контракт:

* `POST /v1/billing/create-checkout-session` (JWT) — находит/создаёт
  Stripe Customer, создаёт Checkout Session на подписку и возвращает
  ``{"url"}`` для редиректа. Уже `pro` → 409 `already_subscribed`.
* `POST /v1/billing/create-portal-session` (JWT) — возвращает ``{"url"}``
  Stripe Customer Portal: управление оплатой/отмена подписки. Нет
  `stripe_customer_id` → 409 `billing_customer_missing`.
* `POST /v1/billing/webhook` — принимает события Stripe **без JWT**:
  подпись `Stripe-Signature` обязательна (`stripe.Webhook.construct_event`).
  `checkout.session.completed` → `users.tier='pro'`,
  `customer.subscription.deleted` → `users.tier='free'`.

URL-адреса success/cancel собираются на сервере из `APP_BASE_URL`, а не
принимаются из тела запроса: иначе клиент мог бы подставить произвольный
внешний redirect (open redirect) и увести пользователя после оплаты.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.billing import BillingGateway, apply_webhook_event, get_billing
from app.config import Settings, get_settings
from app.db.models import User, UserTier
from app.db.session import get_session
from app.errors import (
    BillingAlreadySubscribedError,
    BillingCustomerMissingError,
    BillingNotConfiguredError,
    BillingSignatureError,
)

router = APIRouter(prefix="/billing", tags=["billing"])

logger = structlog.get_logger(__name__)

#: Заголовок подписи Stripe (case-insensitive в Starlette headers).
_SIGNATURE_HEADER = "stripe-signature"


class BillingSessionResponse(BaseModel):
    """URL Stripe Checkout/Portal для редиректа браузера."""

    url: str


class WebhookResponse(BaseModel):
    """Ответ Stripe на webhook; тело не используется, важен 2xx."""

    received: bool = True


def _base_url(settings: Settings) -> str:
    """Публичный URL SPA без завершающего слэша."""
    return settings.app_base_url.rstrip("/")


@router.post(
    "/create-checkout-session",
    response_model=BillingSessionResponse,
    summary="Создать Stripe Checkout Session на подписку Pro",
)
async def create_checkout_session(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    billing: Annotated[BillingGateway, Depends(get_billing)],
) -> BillingSessionResponse:
    """Найти/создать Customer и вернуть URL оплаты подписки.

    `stripe_customer_id` сохраняется сразу после создания клиента: если
    Stripe-сессия не создастся, следующий запрос переиспользует клиента,
    а не наплодит дублей.

    Raises:
        AuthError: нет/битый access-токен (→ 401).
        BillingNotConfiguredError: billing выключен (→ 503).
        BillingAlreadySubscribedError: у пользователя уже `pro` (→ 409).
        BillingError: Stripe недоступен/не вернул URL (→ 502).
    """
    if not billing.configured:
        # Не полагаемся только на настройки: в тестах шлюз может быть
        # подменён, и «настроен» — его свойство.
        raise BillingNotConfiguredError(
            "Оплата временно недоступна: Stripe не настроен"
        )
    if user.tier == UserTier.pro.value:
        raise BillingAlreadySubscribedError(
            "Подписка Pro уже активна — управляйте ею в профиле"
        )

    customer_id = user.stripe_customer_id
    if not customer_id:
        customer_id = await billing.find_or_create_customer(
            user_id=user.id, email=user.email
        )
        user.stripe_customer_id = customer_id
        await session.commit()

    url = await billing.create_checkout_session(
        user_id=user.id,
        customer_id=customer_id,
        success_url=f"{_base_url(settings)}/billing/success",
        cancel_url=f"{_base_url(settings)}/billing/cancel",
    )
    logger.info(
        "billing_checkout_created",
        user_id=user.id,
        customer_id=customer_id,
    )
    return BillingSessionResponse(url=url)


@router.post(
    "/create-portal-session",
    response_model=BillingSessionResponse,
    summary="Создать сессию Stripe Customer Portal",
)
async def create_portal_session(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    billing: Annotated[BillingGateway, Depends(get_billing)],
) -> BillingSessionResponse:
    """Вернуть URL Customer Portal для управления подпиской.

    Raises:
        AuthError: нет/битый access-токен (→ 401).
        BillingNotConfiguredError: billing выключен (→ 503).
        BillingCustomerMissingError: пользователь ещё не создавал Customer
            (→ 409) — сначала нужен checkout.
        BillingError: Stripe недоступен/не вернул URL (→ 502).
    """
    if not billing.configured:
        raise BillingNotConfiguredError(
            "Управление подпиской временно недоступно: Stripe не настроен"
        )
    if not user.stripe_customer_id:
        raise BillingCustomerMissingError(
            "У вас ещё нет подписки Stripe — сначала оформите Pro"
        )
    url = await billing.create_portal_session(
        customer_id=user.stripe_customer_id,
        return_url=f"{_base_url(settings)}/profile",
    )
    logger.info("billing_portal_created", user_id=user.id)
    return BillingSessionResponse(url=url)


@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Webhook Stripe: активация/отмена подписки",
)
async def stripe_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    billing: Annotated[BillingGateway, Depends(get_billing)],
) -> WebhookResponse:
    """Проверить подпись и применить событие к `users.tier`.

    Подпись проверяется до любой работы с БД: без `STRIPE_WEBHOOK_SECRET`
    endpoint отвечает 503, без/с неверным `Stripe-Signature` — 400. Тело
    читается как bytes — Stripe подписывает именно сырые байты, поэтому
    JSON-парсинг до проверки недопустим.

    Raises:
        BillingNotConfiguredError: не задан секрет webhook (→ 503).
        BillingSignatureError: подпись отсутствует/неверна (→ 400).
    """
    if not billing.webhook_configured:
        raise BillingNotConfiguredError(
            "Stripe webhook не настроен: задайте STRIPE_WEBHOOK_SECRET"
        )
    payload = await request.body()
    signature = request.headers.get(_SIGNATURE_HEADER, "")
    if not signature:
        raise BillingSignatureError("Отсутствует заголовок Stripe-Signature")
    event = billing.parse_webhook(payload, signature)
    await apply_webhook_event(event, session)
    return WebhookResponse()
