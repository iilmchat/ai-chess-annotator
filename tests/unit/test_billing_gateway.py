"""Stripe-шлюз без сети: аргументы SDK, поиск Customer, подпись webhook (T-083).

Сеть не нужна: методы `stripe.*` подменяются `monkeypatch`, а подпись
webhook считается тем же HMAC-SHA256, что и у Stripe. Проверяется, что
реальные параметры Checkout/Portal совпадают с контрактом, а неверная
подпись/dev-конфиг отклоняются до БД.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
import stripe

from app.billing import StripeBilling, create_billing
from app.config import Settings
from app.errors import (
    BillingError,
    BillingNotConfiguredError,
    BillingSignatureError,
)

WEBHOOK_SECRET = "whsec_unit_test_secret"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "stripe_secret_key": "sk_test_unit",
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "stripe_price_id": "price_pro_unit",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _signature(payload: bytes, *, timestamp: int | None = None) -> str:
    """Подпись Stripe: ``t=<ts>,v1=hex(hmac_sha256(secret, ts.payload))``."""
    moment = timestamp if timestamp is not None else int(time.time())
    signed = f"{moment}.".encode() + payload
    digest = hmac.new(
        WEBHOOK_SECRET.encode(), signed, hashlib.sha256
    ).hexdigest()
    return f"t={moment},v1={digest}"


def test_create_billing_returns_stripe_gateway() -> None:
    billing = create_billing(_settings())

    assert isinstance(billing, StripeBilling)
    assert billing.configured is True
    assert billing.webhook_configured is True


def test_billing_without_price_is_not_configured() -> None:
    billing = StripeBilling(_settings(stripe_price_id=""))

    assert billing.configured is False


def test_find_or_create_customer_reuses_existing_id() -> None:
    billing = StripeBilling(_settings())

    # Ни одного вызова Stripe: id уже сохранён в users.stripe_customer_id.
    customer_id = asyncio.run(
        billing.find_or_create_customer(
            user_id=1,
            email="user@example.com",
            existing_customer_id="cus_existing",
        )
    )

    assert customer_id == "cus_existing"


def test_find_or_create_customer_finds_by_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_list(**params: Any) -> SimpleNamespace:
        calls.append(params)
        return SimpleNamespace(data=[SimpleNamespace(id="cus_found")])

    def fail_create(**params: Any) -> SimpleNamespace:
        raise AssertionError("create не должен вызываться, если клиент найден")

    monkeypatch.setattr(stripe.Customer, "list", fake_list)
    monkeypatch.setattr(stripe.Customer, "create", fail_create)
    billing = StripeBilling(_settings())

    customer_id = asyncio.run(
        billing.find_or_create_customer(
            user_id=42, email="user@example.com"
        )
    )

    assert customer_id == "cus_found"
    assert calls == [{"email": "user@example.com", "limit": 1}]


def test_find_or_create_customer_creates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(
        stripe.Customer,
        "list",
        lambda **_params: SimpleNamespace(data=[]),
    )

    def fake_create(**params: Any) -> SimpleNamespace:
        created.append(params)
        return SimpleNamespace(id="cus_new")

    monkeypatch.setattr(stripe.Customer, "create", fake_create)
    billing = StripeBilling(_settings())

    customer_id = asyncio.run(
        billing.find_or_create_customer(
            user_id=42, email="user@example.com"
        )
    )

    assert customer_id == "cus_new"
    assert created == [
        {"email": "user@example.com", "metadata": {"user_id": "42"}}
    ]


def test_create_checkout_session_passes_subscription_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**params: Any) -> SimpleNamespace:
        captured.update(params)
        return SimpleNamespace(url="https://checkout.stripe.test/session")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    billing = StripeBilling(_settings())

    url = asyncio.run(
        billing.create_checkout_session(
            user_id=7,
            customer_id="cus_7",
            success_url="https://app.test/billing/success",
            cancel_url="https://app.test/billing/cancel",
        )
    )

    assert url == "https://checkout.stripe.test/session"
    assert captured["mode"] == "subscription"
    assert captured["customer"] == "cus_7"
    assert captured["line_items"] == [
        {"price": "price_pro_unit", "quantity": 1}
    ]
    assert captured["success_url"] == "https://app.test/billing/success"
    assert captured["cancel_url"] == "https://app.test/billing/cancel"
    assert captured["client_reference_id"] == "7"
    assert captured["metadata"] == {"user_id": "7"}
    assert captured["subscription_data"] == {"metadata": {"user_id": "7"}}


def test_create_checkout_session_requires_configuration() -> None:
    billing = StripeBilling(_settings(stripe_secret_key=""))

    with pytest.raises(BillingNotConfiguredError):
        asyncio.run(
            billing.create_checkout_session(
                user_id=1,
                customer_id="cus_1",
                success_url="https://app.test/s",
                cancel_url="https://app.test/c",
            )
        )


def test_create_checkout_session_wraps_stripe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create(**_params: Any) -> SimpleNamespace:
        raise stripe.StripeError("boom")

    monkeypatch.setattr(stripe.checkout.Session, "create", fail_create)
    billing = StripeBilling(_settings())

    with pytest.raises(BillingError):
        asyncio.run(
            billing.create_checkout_session(
                user_id=1,
                customer_id="cus_1",
                success_url="https://app.test/s",
                cancel_url="https://app.test/c",
            )
        )


def test_create_portal_session_passes_customer_and_return_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**params: Any) -> SimpleNamespace:
        captured.update(params)
        return SimpleNamespace(url="https://billing.stripe.test/portal")

    monkeypatch.setattr(stripe.billing_portal.Session, "create", fake_create)
    billing = StripeBilling(_settings())

    url = asyncio.run(
        billing.create_portal_session(
            customer_id="cus_9", return_url="https://app.test/profile"
        )
    )

    assert url == "https://billing.stripe.test/portal"
    assert captured == {
        "customer": "cus_9",
        "return_url": "https://app.test/profile",
    }


def test_parse_webhook_accepts_valid_signature() -> None:
    payload = json.dumps(
        {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_1"}},
        }
    ).encode()
    billing = StripeBilling(_settings())

    event = billing.parse_webhook(payload, _signature(payload))

    assert event["type"] == "checkout.session.completed"
    assert event["data"]["object"]["id"] == "cs_1"


def test_parse_webhook_rejects_invalid_signature() -> None:
    payload = b'{"type": "checkout.session.completed"}'
    billing = StripeBilling(_settings())

    with pytest.raises(BillingSignatureError):
        billing.parse_webhook(payload, "t=1,v1=deadbeef")


def test_parse_webhook_requires_webhook_secret() -> None:
    billing = StripeBilling(_settings(stripe_webhook_secret=""))

    with pytest.raises(BillingNotConfiguredError):
        billing.parse_webhook(b"{}", "t=1,v1=deadbeef")
