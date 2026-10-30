"""Stripe billing end-to-end на реальном приложении и PostgreSQL (T-083).

Проверяется контракт:

* `POST /v1/billing/create-checkout-session` — JWT обязателен; при
  отсутствии Stripe-конфига 503; создаёт/находит Customer, сохраняет
  `users.stripe_customer_id`, возвращает `{url}`; повторный вызов
  переиспользует клиента; для `pro` — 409.
* `POST /v1/billing/webhook` — подпись `Stripe-Signature` проверяется
  реальным `stripe.Webhook.construct_event`; `checkout.session.completed`
  делает `tier='pro'`, `customer.subscription.deleted` — `tier='free'`;
  события идемпотентны, неизвестный customer не ломает 200.
* `POST /v1/billing/create-portal-session` — нет Customer → 409, иначе
  возвращается URL Stripe Customer Portal.

Сеть к Stripe не ходит: методы SDK подменяются `monkeypatch`, поэтому тест
проверяет и реальную проверку подписи, и аргументы вызовов. Требуются
PostgreSQL (пользователи) и `STRIPE_WEBHOOK_SECRET`; без БД — skip.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import User
from app.main import create_app

PASSWORD = "billing-password-123"
WEBHOOK_SECRET = "whsec_integration_billing_secret"


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


def _settings(**overrides: Any) -> Settings:
    base = get_settings().model_copy(
        update={
            "app_env": "test",
            "test_auth_bypass": False,
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "prompt_pubsub_enabled": False,
            "semantic_cache_enabled": False,
            "engine_cache_enabled": False,
            "otel_enabled": False,
            "app_base_url": "http://testserver",
            "stripe_secret_key": "sk_test_integration",
            "stripe_webhook_secret": WEBHOOK_SECRET,
            "stripe_price_id": "price_pro_integration",
        }
    )
    return base.model_copy(update=overrides)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


def _email() -> str:
    return f"billing-{uuid4().hex}@example.com"


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _read_user(email: str) -> tuple[str, str | None]:
    """Прочитать `(tier, stripe_customer_id)` из БД."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            return user.tier, user.stripe_customer_id
    finally:
        await engine.dispose()


async def _set_user(
    email: str, *, tier: str | None = None, customer: str | None = None
) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            if tier is not None:
                user.tier = tier
            if customer is not None:
                user.stripe_customer_id = customer
            await session.commit()
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


def _signature(payload: bytes, *, secret: str = WEBHOOK_SECRET) -> str:
    moment = int(time.time())
    signed = f"{moment}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


def _checkout_event(
    *, user_id: int, customer: str, event_id: str | None = None
) -> dict[str, Any]:
    return {
        "id": event_id or f"evt_{uuid4().hex}",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "object": "checkout.session",
                "customer": customer,
                "client_reference_id": str(user_id),
                "metadata": {"user_id": str(user_id)},
            }
        },
    }


def _deleted_event(*, customer: str) -> dict[str, Any]:
    return {
        "id": f"evt_{uuid4().hex}",
        "object": "event",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": "sub_test_1",
                "object": "subscription",
                "customer": customer,
                "status": "canceled",
            }
        },
    }


def _post_webhook(
    client: TestClient, event: dict[str, Any], *, signature: str | None = None
) -> Any:
    payload = json.dumps(event).encode()
    headers = {
        "Stripe-Signature": signature or _signature(payload),
        "Content-Type": "application/json",
    }
    return client.post(
        "/v1/billing/webhook", content=payload, headers=headers
    )


# --- Checkout -------------------------------------------------------------


def test_checkout_requires_authentication(client: TestClient) -> None:
    response = client.post("/v1/billing/create-checkout-session")

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


def test_checkout_returns_503_when_stripe_not_configured() -> None:
    with TestClient(
        create_app(_settings(stripe_secret_key="", stripe_price_id=""))
    ) as client:
        email = _email()
        try:
            _register(client, email)
            token = _login(client, email)
            response = client.post(
                "/v1/billing/create-checkout-session",
                headers=_headers(token),
            )
        finally:
            asyncio.run(_delete_users([email]))

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "billing_not_configured"


def test_checkout_creates_customer_persists_and_reuses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = _email()
    created: list[dict[str, Any]] = []

    monkeypatch.setattr(
        stripe.Customer,
        "list",
        lambda **_params: SimpleNamespace(data=[]),
    )

    def fake_customer_create(**params: Any) -> SimpleNamespace:
        created.append(params)
        return SimpleNamespace(id="cus_new_1")

    monkeypatch.setattr(stripe.Customer, "create", fake_customer_create)
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **_params: SimpleNamespace(
            url="https://checkout.stripe.test/cs_1"
        ),
    )
    try:
        user = _register(client, email)
        token = _login(client, email)
        headers = _headers(token)

        first = client.post(
            "/v1/billing/create-checkout-session", headers=headers
        )
        second = client.post(
            "/v1/billing/create-checkout-session", headers=headers
        )

        assert first.status_code == 200, first.text
        assert first.json() == {"url": "https://checkout.stripe.test/cs_1"}
        assert second.status_code == 200, second.text
        # Customer создан один раз, второй checkout переиспользует id.
        assert len(created) == 1
        assert created[0] == {
            "email": email,
            "metadata": {"user_id": str(user["id"])},
        }
        tier, customer = asyncio.run(_read_user(email))
        assert tier == "free"
        assert customer == "cus_new_1"
    finally:
        asyncio.run(_delete_users([email]))


def test_checkout_rejects_already_pro(client: TestClient) -> None:
    email = _email()
    try:
        _register(client, email)
        token = _login(client, email)
        asyncio.run(_set_user(email, tier="pro", customer="cus_pro"))

        response = client.post(
            "/v1/billing/create-checkout-session",
            headers=_headers(token),
        )
    finally:
        asyncio.run(_delete_users([email]))

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "already_subscribed"


# --- Webhook --------------------------------------------------------------


def test_webhook_checkout_completed_activates_pro(
    client: TestClient,
) -> None:
    email = _email()
    try:
        user = _register(client, email)
        customer = f"cus_{uuid4().hex}"
        event = _checkout_event(user_id=int(user["id"]), customer=customer)

        response = _post_webhook(client, event)
        assert response.status_code == 200, response.text
        assert response.json() == {"received": True}

        tier, stored_customer = asyncio.run(_read_user(email))
        assert tier == "pro"
        assert stored_customer == customer

        # Повторная доставка того же события не ломает состояние.
        repeated = _post_webhook(client, event)
        assert repeated.status_code == 200, repeated.text
        tier_again, _ = asyncio.run(_read_user(email))
        assert tier_again == "pro"
    finally:
        asyncio.run(_delete_users([email]))


def test_webhook_subscription_deleted_returns_free(
    client: TestClient,
) -> None:
    email = _email()
    customer = f"cus_{uuid4().hex}"
    try:
        _register(client, email)
        asyncio.run(_set_user(email, tier="pro", customer=customer))

        response = _post_webhook(client, _deleted_event(customer=customer))

        assert response.status_code == 200, response.text
        tier, stored_customer = asyncio.run(_read_user(email))
        assert tier == "free"
        # Customer не стираем: повторный checkout переиспользует его.
        assert stored_customer == customer
    finally:
        asyncio.run(_delete_users([email]))


def test_webhook_unknown_customer_is_accepted(
    client: TestClient,
) -> None:
    response = _post_webhook(
        client, _deleted_event(customer="cus_does_not_exist")
    )

    # 200, чтобы Stripe не ретраил заведомо «чужое» событие бесконечно.
    assert response.status_code == 200, response.text


def test_webhook_rejects_invalid_signature(client: TestClient) -> None:
    payload = json.dumps(_deleted_event(customer="cus_x")).encode()
    response = client.post(
        "/v1/billing/webhook",
        content=payload,
        headers={
            "Stripe-Signature": "t=1,v1=deadbeef",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "billing_invalid_signature"


def test_webhook_requires_signature_header(client: TestClient) -> None:
    response = client.post(
        "/v1/billing/webhook",
        content=json.dumps(_deleted_event(customer="cus_x")).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "billing_invalid_signature"


def test_webhook_returns_503_without_secret() -> None:
    with TestClient(
        create_app(_settings(stripe_webhook_secret=""))
    ) as client:
        response = client.post(
            "/v1/billing/webhook",
            content=b"{}",
            headers={
                "Stripe-Signature": "t=1,v1=deadbeef",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 503, response.text
    assert response.json()["code"] == "billing_not_configured"


# --- Customer Portal ------------------------------------------------------


def test_portal_requires_existing_customer(client: TestClient) -> None:
    email = _email()
    try:
        _register(client, email)
        token = _login(client, email)
        response = client.post(
            "/v1/billing/create-portal-session", headers=_headers(token)
        )
    finally:
        asyncio.run(_delete_users([email]))

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "billing_customer_missing"


def test_portal_returns_stripe_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = _email()
    captured: dict[str, Any] = {}

    def fake_portal(**params: Any) -> SimpleNamespace:
        captured.update(params)
        return SimpleNamespace(url="https://billing.stripe.test/portal")

    monkeypatch.setattr(stripe.billing_portal.Session, "create", fake_portal)
    try:
        _register(client, email)
        token = _login(client, email)
        asyncio.run(_set_user(email, tier="pro", customer="cus_portal"))

        response = client.post(
            "/v1/billing/create-portal-session", headers=_headers(token)
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"url": "https://billing.stripe.test/portal"}
        assert captured == {
            "customer": "cus_portal",
            "return_url": "http://testserver/profile",
        }
    finally:
        asyncio.run(_delete_users([email]))


def test_usage_lists_db_tier_for_pro_user(client: TestClient) -> None:
    """T-083/FinOps: usage-эндпоинт читает тариф из БД, а не из роли."""
    email = _email()
    try:
        _register(client, email)
        token = _login(client, email)
        asyncio.run(_set_user(email, tier="pro"))

        response = client.get(
            "/v1/users/me/usage", headers=_headers(token)
        )
        me = client.get("/auth/me", headers=_headers(token))

        assert response.status_code == 200, response.text
        assert me.status_code == 200, me.text
        assert me.json()["tier"] == "pro"
        body = response.json()
        assert body["tier"] == "pro"
        assert body["limit_tokens"] == 500_000
    finally:
        asyncio.run(_delete_users([email]))
