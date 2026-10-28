"""Проводка TokenBudgetMiddleware в реальном приложении (T-031).

Лимит 0 позволяет проверить 429 без Stockfish/LM Studio: middleware
срабатывает раньше эндпоинта.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    settings = get_settings().model_copy(
        update={
            "token_budget_enabled": True,
            "daily_token_limit_free": 0,
            "daily_token_limit_pro": 0,
            "rate_limit_enabled": False,
            "llm_warmup_enabled": False,
            "otel_enabled": False,
        }
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_analyze_is_blocked_when_daily_budget_is_zero(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/analyze", json={"pgn": "1. e4 e5", "level": "club"}
    )

    assert response.status_code == 429
    assert response.json()["code"] == "token_budget_exceeded"
    assert "X-RateLimit-Reset" in response.headers
    assert response.headers["X-Token-Budget-Period"] == "day"
