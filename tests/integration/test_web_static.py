"""Интеграционные тесты SPA-раздачи (T-025).

Проверяют, что production-сборка `web/dist` монтируется тем же FastAPI и не
перехватывает API. Если фронтенд ещё не собран (например, в backend-CI),
тест скипается.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def _client() -> TestClient:
    settings = get_settings().model_copy(
        update={"rate_limit_requests": 1000, "llm_warmup_enabled": False}
    )
    return TestClient(create_app(settings))


@pytest.mark.skipif(
    not (WEB_DIST / "index.html").is_file(),
    reason="web/dist не собран (сначала pnpm --dir web build)",
)
def test_spa_root_serves_index_and_api_stays_available() -> None:
    with _client() as client:
        root = client.get("/")
        api = client.get("/v1/health")

    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert '<div id="root">' in root.text
    assert api.status_code == 200
    assert "status" in api.json()
