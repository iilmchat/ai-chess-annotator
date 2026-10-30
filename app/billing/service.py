"""Обработка событий Stripe: активация/деактивация Pro (T-083).

События приходят только после проверки подписи в `StripeBilling.parse_webhook`.
Обработчик идемпотентен: Stripe доставляет webhook повторно при таймаутах и
5xx, а `checkout.session.completed` может прийти несколько раз. Поэтому
операции — это `UPDATE ... WHERE`, а не «прибавить/создать»: повторное
событие не меняет результат.

Поддерживаемые события:

* ``checkout.session.completed`` — оплата/подписка оформлена: `tier='pro'`,
  сохраняем `stripe_customer_id` (если его ещё не было);
* ``customer.subscription.deleted`` — подписка отменена/истекла:
  `tier='free'`. `stripe_customer_id` не стираем: клиент остаётся в Stripe,
  и повторный checkout переиспользует его, а не создаёт дубль.

Неизвестные события логируются и игнорируются (200), чтобы Stripe не
повторял их бесконечно.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User, UserTier

__all__ = ["apply_webhook_event"]

logger = structlog.get_logger(__name__)

_CHECKOUT_COMPLETED = "checkout.session.completed"
_SUBSCRIPTION_DELETED = "customer.subscription.deleted"


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Поле StripeObject/dict без падения на неожиданном типе."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_int(value: Any) -> int | None:
    """Положительный int из строки/числа (id пользователя)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _checkout_user_id(obj: Any) -> int | None:
    """user_id из `client_reference_id` или metadata Checkout Session."""
    metadata = _field(obj, "metadata", {}) or {}
    return _as_int(_field(obj, "client_reference_id")) or _as_int(
        _field(metadata, "user_id")
    )


async def _find_user(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    customer_id: str | None = None,
) -> User | None:
    """Найти пользователя по id (приоритет) или Stripe Customer id."""
    if user_id is not None:
        user = await session.get(User, user_id)
        if user is not None:
            return user
    if customer_id:
        return await session.scalar(
            select(User).where(User.stripe_customer_id == customer_id)
        )
    return None


async def _activate_pro(
    session: AsyncSession, obj: Any
) -> bool:
    """`checkout.session.completed` → `tier='pro'` и customer id."""
    customer_id = _field(obj, "customer")
    customer = str(customer_id) if customer_id else None
    user_id = _checkout_user_id(obj)
    user = await _find_user(
        session, user_id=user_id, customer_id=customer
    )
    if user is None:
        logger.warning(
            "billing_checkout_user_not_found",
            user_id=user_id,
            customer_id=customer,
        )
        return False
    changed = False
    if user.tier != UserTier.pro.value:
        user.tier = UserTier.pro.value
        changed = True
    if customer and user.stripe_customer_id != customer:
        user.stripe_customer_id = customer
        changed = True
    if changed:
        await session.commit()
    logger.info(
        "billing_subscription_activated",
        user_id=user.id,
        customer_id=user.stripe_customer_id,
        changed=changed,
    )
    return True


async def _deactivate_pro(session: AsyncSession, obj: Any) -> bool:
    """`customer.subscription.deleted` → `tier='free'` (customer сохраняем)."""
    customer_id = _field(obj, "customer")
    customer = str(customer_id) if customer_id else None
    user = await _find_user(session, customer_id=customer)
    if user is None:
        logger.warning(
            "billing_subscription_user_not_found", customer_id=customer
        )
        return False
    changed = user.tier != UserTier.free.value
    if changed:
        user.tier = UserTier.free.value
        await session.commit()
    logger.info(
        "billing_subscription_cancelled",
        user_id=user.id,
        customer_id=customer,
        changed=changed,
    )
    return True


async def apply_webhook_event(
    event: Mapping[str, Any], session: AsyncSession
) -> bool:
    """Применить событие Stripe к БД; вернуть `True`, если оно обработано.

    Идемпотентно: повторный `checkout.session.completed` не меняет тариф
    второй раз, повторная отмена — тоже. Неизвестный тип не ошибка.
    """
    event_type = str(_field(event, "type", ""))
    data = _field(event, "data", {}) or {}
    obj = _field(data, "object", {}) or {}

    if event_type == _CHECKOUT_COMPLETED:
        return await _activate_pro(session, obj)
    if event_type == _SUBSCRIPTION_DELETED:
        return await _deactivate_pro(session, obj)

    logger.info("billing_event_ignored", event_type=event_type)
    return False
