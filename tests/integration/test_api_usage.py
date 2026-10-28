"""`GET /v1/users/me/usage` — дневной бюджет токенов (T-037)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.finops import TokenBudgetStatus
from app.main import create_app


class FakeTracker:
    async def check(self, subject: str, tier: str) -> TokenBudgetStatus:
        assert subject.startswith("ip:")
        assert tier == "free"
        return TokenBudgetStatus(
            allowed=True,
            limit=100,
            used=40,
            remaining=60,
            reset_at=1_800_000_000,
            retry_after_s=3600,
        )


def _settings() -> Settings:
    return Settings(
        daily_token_limit_free=100,
        daily_token_limit_pro=1000,
        token_budget_enabled=False,
        rate_limit_enabled=False,
        prompt_pubsub_enabled=False,
        semantic_cache_enabled=False,
        llm_warmup_enabled=False,
        otel_enabled=False,
        admin_api_key="test-admin-token",
    )


def test_usage_returns_tracker_values() -> None:
    with TestClient(create_app(_settings())) as client:
        client.app.state.token_usage_tracker = FakeTracker()  # type: ignore[attr-defined]
        response = client.get("/v1/users/me/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["used_tokens"] == 40
    assert body["limit_tokens"] == 100
    assert body["remaining_tokens"] == 60
    assert body["enabled"] is True


def test_usage_fallback_when_budget_disabled() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get("/v1/users/me/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["limit_tokens"] == 100
    assert body["used_tokens"] == 0
