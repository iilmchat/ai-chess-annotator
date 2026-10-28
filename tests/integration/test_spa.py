"""SPA fallback для `/history` и `/analysis/:id` (T-036)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="web/dist не собран",
)


def _client() -> TestClient:
    cfg = Settings(
        rate_limit_enabled=False,
        token_budget_enabled=False,
        semantic_cache_enabled=False,
        llm_warmup_enabled=False,
        otel_enabled=False,
        admin_api_key="test-admin-token",
    )
    return TestClient(create_app(cfg))


def test_spa_routes_return_index() -> None:
    with _client() as client:
        for path in ("/", "/history", "/analysis/42"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert 'id="root"' in response.text


def test_unknown_api_path_stays_404() -> None:
    with _client() as client:
        response = client.get("/v1/definitely-missing")

    assert response.status_code == 404
