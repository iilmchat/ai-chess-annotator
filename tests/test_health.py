import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok_or_degraded(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert set(body["components"]) == {"db", "redis", "stockfish", "llm"}
    for comp in body["components"].values():
        assert comp["status"] in {"ok", "error", "skipped"}