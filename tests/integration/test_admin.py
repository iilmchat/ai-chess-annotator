"""Админ-панель `/admin` (T-028).

Проверяем control plane целиком:

* JWT-роли ``admin``/``auditor`` пускают (Bearer и cookie ``access_token``),
  роль ``user`` — 403 ``admin_forbidden``;
* битый/просроченный токен и неверный ``Authorization`` — 403;
* без креденшелов — 401 ``admin_unauthorized`` (как раньше);
* legacy ``X-Admin-Token`` и парольная форма остаются рабочими;
* read-only views для RAG/комментариев/правок доступны только после
  аутентификации.

Пользователи создаются напрямую через ORM (пароль не нужен — access-токен
выдаёт ``create_access_token``) и удаляются после теста.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.tokens import create_access_token
from app.config import Settings, get_settings
from app.db.models import AdminRole, User
from app.main import create_app

ADMIN_TOKEN = "test-admin-token"
ADMIN_PASSWORD = "test-admin-password"
AUTH = {"X-Admin-Token": ADMIN_TOKEN}
ROLES = ("admin", "auditor", "user")


@dataclass(frozen=True, slots=True)
class JwtUsers:
    """Созданные в БД пользователи по ролям + их email-ы."""

    users: dict[str, User]
    emails: dict[str, str]


def _db_url() -> str:
    """URL БД из тех же настроек, что использует `app.db.session`."""
    return get_settings().database_url


async def _insert_users(emails: dict[str, str]) -> dict[str, User]:
    """Вставить активных пользователей с заданными ролями (без пароля)."""
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    users: dict[str, User] = {}
    try:
        async with factory() as session:
            for role, email in emails.items():
                user = User(
                    email=email, role=AdminRole(role), is_active=True
                )
                session.add(user)
                await session.flush()
                users[role] = user
            await session.commit()
    finally:
        await engine.dispose()
    return users


async def _set_active(email: str, *, active: bool) -> None:
    """Переключить `is_active` напрямую в БД (сценарий «отключённый admin»)."""
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            user.is_active = active
            await session.commit()
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    """Убрать созданных тестами пользователей (партии у них не создаются)."""
    if not emails:
        return
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


@pytest.fixture()
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "admin_api_key": ADMIN_TOKEN,
            "admin_password": ADMIN_PASSWORD,
            "admin_username": "admin",
            "rate_limit_enabled": False,
            "llm_warmup_enabled": False,
            "otel_enabled": False,
        }
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def jwt_users(
    settings: Settings, client: TestClient
) -> Iterator[JwtUsers]:
    """Пользователи ролей admin/auditor/user для JWT-сценариев.

    Зависит от `client`, чтобы БД была гарантированно инициализирована
    приложением; сами строки удаляются после теста.
    """
    emails = {
        role: f"admin-panel-{role}-{uuid4().hex}@example.com"
        for role in ROLES
    }
    users = asyncio.run(_insert_users(emails))
    try:
        yield JwtUsers(users=users, emails=emails)
    finally:
        asyncio.run(_delete_users(list(emails.values())))


def _bearer(user: User, settings: Settings) -> dict[str, str]:
    """Заголовок `Authorization: Bearer <access JWT>` для пользователя."""
    token = create_access_token(user, settings)
    return {"Authorization": f"Bearer {token}"}


def _expired_token(settings: Settings) -> str:
    """Подписанный, но просроченный access-токен."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "1",
            "role": "admin",
            "type": "access",
            "iat": int((now - timedelta(minutes=2)).timestamp()),
            "exp": now - timedelta(minutes=1),
            "jti": uuid4().hex,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_alg,
    )


# --- Guard: 401/403 -------------------------------------------------------


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


# --- JWT: роли admin/auditor/user -----------------------------------------


def test_admin_accepts_admin_jwt(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    response = client.get(
        "/admin", headers=_bearer(jwt_users.users["admin"], settings)
    )
    assert response.status_code == 200, response.text
    assert "ChessAnnotator Admin" in response.text


def test_admin_accepts_auditor_jwt(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    response = client.get(
        "/admin", headers=_bearer(jwt_users.users["auditor"], settings)
    )
    assert response.status_code == 200, response.text


def test_admin_rejects_user_jwt(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    response = client.get(
        "/admin", headers=_bearer(jwt_users.users["user"], settings)
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "admin_forbidden"
    assert body["detail"] == "Admin role required"


def test_admin_rejects_broken_jwt(client: TestClient) -> None:
    response = client.get(
        "/admin", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


def test_admin_rejects_expired_jwt(
    client: TestClient, settings: Settings
) -> None:
    response = client.get(
        "/admin",
        headers={"Authorization": f"Bearer {_expired_token(settings)}"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


def test_admin_rejects_malformed_authorization_header(
    client: TestClient,
) -> None:
    response = client.get("/admin", headers={"Authorization": "Token abc"})
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


def test_admin_jwt_missing_user_is_forbidden(
    client: TestClient, settings: Settings
) -> None:
    """Подписанный токен на несуществующего пользователя — 403, не 500."""
    ghost = User(
        email=f"ghost-{uuid4().hex}@example.com", role=AdminRole.admin
    )
    ghost.id = 2**62  # заведомо отсутствующий id
    response = client.get("/admin", headers=_bearer(ghost, settings))
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


def test_admin_rejects_inactive_admin_jwt(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    admin = jwt_users.users["admin"]
    asyncio.run(_set_active(admin.email, active=False))
    response = client.get("/admin", headers=_bearer(admin, settings))
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


# --- JWT в cookie access_token --------------------------------------------


def test_admin_accepts_admin_jwt_cookie(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    token = create_access_token(jwt_users.users["admin"], settings)
    client.cookies.set("access_token", token)
    response = client.get("/admin")
    assert response.status_code == 200, response.text
    assert "ChessAnnotator Admin" in response.text


def test_admin_rejects_user_jwt_cookie(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    token = create_access_token(jwt_users.users["user"], settings)
    client.cookies.set("access_token", token)
    response = client.get("/admin")
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


def test_admin_rejects_broken_jwt_cookie(client: TestClient) -> None:
    client.cookies.set("access_token", "not-a-jwt")
    response = client.get("/admin")
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "admin_forbidden"


# --- Парольная форма (legacy fallback) ------------------------------------


def test_admin_login_page_is_public(client: TestClient) -> None:
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert 'name="password"' in response.text


def test_admin_provider_accepts_jwt_on_login_page(
    client: TestClient, jwt_users: JwtUsers, settings: Settings
) -> None:
    """Провайдер (не guard) тоже понимает Bearer: login редиректит в /admin.

    `/admin/login` публичен, поэтому guard не участвует; 303 доказывает,
    что `AdminAuthProvider.authenticate` разобрал JWT и вернул AdminUser.
    """
    response = client.get(
        "/admin/login", headers=_bearer(jwt_users.users["admin"], settings)
    )
    assert response.status_code == 303, response.text
    assert "/admin" in response.headers["location"]


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


# --- Views ------------------------------------------------------------------


def test_admin_registered_views(client: TestClient) -> None:
    for path in (
        "/admin/annotated-game/list",
        "/admin/commentary/list",
        "/admin/edit/list",
        "/admin/llm-evaluation/list",
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
