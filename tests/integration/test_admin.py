"""Админ-панель `/admin` (T-028, частично).

Проверяем базовый control plane: страница защищена токеном, неверный
токен даёт 403, вход по форме поднимает cookie-сессию, а read-only views
для RAG/комментариев/правок доступны только после аутентификации.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

ADMIN_TOKEN = "test-admin-token"
ADMIN_PASSWORD = "test-admin-password"
AUTH = {"X-Admin-Token": ADMIN_TOKEN}


@pytest.fixture()
def client() -> Iterator[TestClient]:
    settings = get_settings().model_copy(
        update={
            "admin_api_key": ADMIN_TOKEN,
            "admin_password": ADMIN_PASSWORD,
            "admin_username": "admin",
            "rate_limit_enabled": False,
            "llm_warmup_enabled": False,
            "otel_enabled": False,
        }
    )
    with TestClient(create_app(settings), follow_redirects=False) as test_client:
        yield test_client


def test_admin_requires_authentication(client: TestClient) -> None:
    response = client.get("/admin")
    assert response.status_code == 401
    assert response.json()["code"] == "admin_unauthorized"


def test_admin_rejects_invalid_token(client: TestClient) -> None:
    response = client.get("/admin", headers={"X-Admin-Token": "wrong-token"})
    assert response.status_code == 403
    assert response.json()["code"] == "admin_forbidden"


def test_admin_accepts_valid_token(client: TestClient) -> None:
    response = client.get("/admin", headers=AUTH)
    assert response.status_code == 200
    assert "ChessAnnotator Admin" in response.text


def test_admin_login_page_is_public(client: TestClient) -> None:
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert 'name="password"' in response.text


def test_admin_login_creates_session(client: TestClient) -> None:
    login_page = client.get("/admin/login")
    match = re.search(
        r'name="csrftoken" value="([^"]+)"', login_page.text
    )
    assert match is not None, "форма логина должна содержать CSRF-токен"

    response = client.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": ADMIN_TOKEN,
            "csrftoken": match.group(1),
        },
    )
    assert response.status_code == 303
    assert "admin_session" in response.cookies

    home = client.get("/admin")
    assert home.status_code == 200


def test_admin_registered_views(client: TestClient) -> None:
    for path in (
        "/admin/annotated-game/list",
        "/admin/commentary/list",
        "/admin/edit/list",
    ):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 200, path


def test_admin_audit_view_and_integrity_page(client: TestClient) -> None:
    listing = client.get("/admin/audit-log/list", headers=AUTH)
    assert listing.status_code == 200

    verify = client.get("/admin/audit/verify", headers=AUTH)
    assert verify.status_code == 200
    assert "Проверить целостность" in verify.text
    assert "OK:" in verify.text


def test_admin_prompt_view_is_editable(client: TestClient) -> None:
    listing = client.get("/admin/prompt/list", headers=AUTH)
    assert listing.status_code == 200
    assert "club" in listing.text

    match = re.search(r"/admin/prompt/edit\?pk=(\d+)", listing.text)
    assert match is not None, "в списке промптов должна быть ссылка на edit"

    edit_page = client.get(
        f"/admin/prompt/edit?pk={match.group(1)}", headers=AUTH
    )
    assert edit_page.status_code == 200
    assert 'name="content"' in edit_page.text
