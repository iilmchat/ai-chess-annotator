"""Интеграционные тесты `/auth` с реальной JWT-аутентификацией (T-028).

Старая сюита ходит в API анонимно через тестовый seam
(`APP_ENV=test` + `TEST_AUTH_BYPASS=true`). Здесь наоборот: приложение
собирается с `test_auth_bypass=False`, поэтому зависимости
`get_current_user`/`get_current_user_optional` не подменяются и проверяется
настоящая цепочка «регистрация → вход → cookie/токены → профиль → logout».

KI-036: пароли хешируются Argon2id, legacy bcrypt-хеш мигрирует при входе.
KI-037: refresh-токен — в HttpOnly-cookie (в теле ответа его нет), logout
отзывает `jti` через Redis-denylist.

Тесты требуют PostgreSQL (как и остальные интеграционные), импортируемая
схема — миграция `c7d8e9f0a1b2`. Тесты logout/denylist/rate-limit требуют
Redis и пропускаются без него.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.denylist import denylist_key
from app.auth.passwords import verify_password
from app.auth.tokens import decode_token
from app.config import Settings, get_settings
from app.db.models import User
from app.main import create_app

#: Пароль, удовлетворяющий схеме (8..256 символов).
PASSWORD = "auth-api-password-123"
#: Заведомо неверный пароль (тоже проходит схему).
WRONG_PASSWORD = "definitely-wrong-password"
#: Значение по умолчанию `access_token_ttl_min` (15 минут) в секундах.
ACCESS_TTL_S = 15 * 60
#: Значение по умолчанию `auth_cookie_name`.
COOKIE_NAME = "refresh_token"
#: Значение по умолчанию `refresh_token_ttl_days` в секундах (Max-Age cookie).
REFRESH_MAX_AGE_S = 7 * 24 * 60 * 60

PROFILE_KEYS = {
    "id",
    "email",
    "level",
    "role",
    "is_active",
    "is_verified",
    "created_at",
}
ACCESS_RESPONSE_KEYS = {"access_token", "token_type", "expires_in", "user"}

#: Готовый legacy bcrypt-хеш `PASSWORD` (cost 12) из БД итерации 24.
LEGACY_BCRYPT_HASH = (
    "$2b$12$3j66cRjBDoswAXUuG1JyJuBZvaTsjRd3bHSvS6uDfTM9tn4mPvqV6"
)


def _db_available() -> bool:
    """Проверить доступность PostgreSQL (как в остальных интеграционных тестах)."""

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


def _redis_available() -> bool:
    """Проверить доступность Redis (denylist/logout и rate limit без него не тестируем)."""

    async def ping() -> None:
        client = aioredis.from_url(
            get_settings().redis_url, decode_responses=True
        )
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)
requires_redis = pytest.mark.skipif(
    not _redis_available(), reason="Redis недоступен"
)


def _settings(**overrides: Any) -> Settings:
    """Настройки приложения с ВЫКЛЮЧЕННЫМ тестовым bypass-ом авторизации."""
    base: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "test_auth_bypass": False,
        "rate_limit_enabled": False,
        "token_budget_enabled": False,
        "llm_warmup_enabled": False,
        "prompt_pubsub_enabled": False,
        "semantic_cache_enabled": False,
        "cors_origins": "http://localhost:5173",
        # PyJWT предупреждает про HMAC-ключи < 32 байт; фиксируем длинный.
        "jwt_secret": "integration-test-secret-0123456789-0123456789",
    }
    base.update(overrides)
    return Settings(**base)


def _client(**overrides: Any) -> TestClient:
    """TestClient с реальной аутентификацией."""
    return TestClient(create_app(_settings(**overrides)))


def _email() -> str:
    """Уникальный email: таблица `users` общая, пустой она не считается."""
    return f"auth-api-{uuid4().hex}@example.com"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(
    client: TestClient, email: str, password: str = PASSWORD
) -> dict[str, Any]:
    response = client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(
    client: TestClient, email: str, password: str = PASSWORD
) -> dict[str, Any]:
    response = client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _set_active(email: str, *, active: bool) -> None:
    """Переключить `is_active` напрямую в БД (сценарий «отключённый user»)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
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


async def _set_password_hash(email: str, password_hash: str) -> None:
    """Подменить хеш пароля в БД (сценарий legacy bcrypt-хеша из итерации 24)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            user.password_hash = password_hash
            await session.commit()
    finally:
        await engine.dispose()


async def _read_user(email: str) -> tuple[str | None, bool, datetime | None]:
    """Прочитать `(password_hash, is_active, last_login_at)` из БД."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            return user.password_hash, user.is_active, user.last_login_at
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    """Убрать созданных тестами пользователей (партии у них не создаются)."""
    if not emails:
        return
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


async def _delete_denylist_keys(jtis: list[str]) -> None:
    """Убрать ключи denylist, созданные тестом (Redis не должен «течь»)."""
    if not jtis:
        return
    client = aioredis.from_url(
        get_settings().redis_url, decode_responses=True
    )
    try:
        await client.delete(*(denylist_key(jti) for jti in jtis))
    finally:
        await client.aclose()


def _jti(refresh_token: str) -> str:
    """Достать `jti` refresh-токена, подписанного тестовым приложением."""
    return decode_token(
        refresh_token, _settings(), expected_type="refresh"
    ).jti


def _set_cookie_headers(response: Any) -> list[str]:
    """Все `Set-Cookie` ответа (httpx get_list; иначе — одиночный заголовок)."""
    get_list = getattr(response.headers, "get_list", None)
    if callable(get_list):
        return list(get_list("set-cookie"))
    header = response.headers.get("set-cookie")
    return [header] if header else []


def _assert_refresh_cookie(
    response: Any, *, max_age: int = REFRESH_MAX_AGE_S
) -> str:
    """Проверить флаги refresh-cookie и вернуть её значение."""
    headers = _set_cookie_headers(response)
    assert headers, "в ответе нет Set-Cookie с refresh-токеном"
    header = headers[0]
    assert f"{COOKIE_NAME}=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/auth" in header
    assert f"Max-Age={max_age}" in header
    assert "Secure" not in header
    cookie_value = header.split(";", 1)[0].split("=", 1)[1].strip('"')
    assert cookie_value
    return cookie_value


def _assert_cleared_cookie(response: Any) -> None:
    """Проверить, что logout удаляет refresh-cookie (`Max-Age=0`)."""
    headers = _set_cookie_headers(response)
    assert headers, "logout не прислал Set-Cookie для очистки"
    header = headers[0]
    assert f"{COOKIE_NAME}=" in header
    assert "Max-Age=0" in header
    assert "Path=/auth" in header


# --- Регистрация ----------------------------------------------------------


def test_register_returns_profile_and_lowercases_email() -> None:
    email = f"Auth-{uuid4().hex}@Example.COM"
    try:
        with _client() as client:
            response = client.post(
                "/auth/register",
                json={"email": email, "password": PASSWORD},
            )

        assert response.status_code == 201, response.text
        body = response.json()
        assert set(body) == PROFILE_KEYS
        assert body["id"] > 0
        assert body["email"] == email.lower()
        assert body["level"] == "club"
        assert body["role"] == "user"
        assert body["is_active"] is True
        # KI-041: регистрация создаёт неподтверждённого пользователя.
        assert body["is_verified"] is False
        assert datetime.fromisoformat(body["created_at"]) is not None

        password_hash, _, _ = asyncio.run(_read_user(email.lower()))
        assert password_hash is not None
        assert password_hash.startswith("$argon2id$")
    finally:
        asyncio.run(_delete_users([email.lower()]))


def test_register_duplicate_email_returns_409() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            # Другой регистр — тот же email после нормализации.
            response = client.post(
                "/auth/register",
                json={"email": email.upper(), "password": PASSWORD},
            )

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "email_taken"
        assert body["detail"]
    finally:
        asyncio.run(_delete_users([email]))


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "short-password@example.com", "password": "short"},
        {"email": "long-password@example.com", "password": "a" * 257},
    ],
)
def test_register_rejects_invalid_input(payload: dict[str, str]) -> None:
    with _client() as client:
        response = client.post("/auth/register", json=payload)

    assert response.status_code == 422, response.text


# --- Вход -----------------------------------------------------------------


def test_login_returns_access_token_and_sets_httponly_cookie() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            response = client.post(
                "/auth/login",
                json={"email": email.upper(), "password": PASSWORD},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == ACCESS_RESPONSE_KEYS
            assert body["access_token"]
            # KI-037: refresh НЕ в теле — только в HttpOnly-cookie.
            assert "refresh_token" not in body
            assert body["token_type"] == "bearer"
            assert body["expires_in"] == ACCESS_TTL_S
            assert set(body["user"]) == PROFILE_KEYS
            assert body["user"]["email"] == email
            assert body["user"]["role"] == "user"

            cookie_value = _assert_refresh_cookie(response)
            assert cookie_value != body["access_token"]
            assert client.cookies.get(COOKIE_NAME) == cookie_value

        password_hash, is_active, last_login_at = asyncio.run(
            _read_user(email)
        )
        assert password_hash is not None
        assert password_hash.startswith("$argon2id$")
        assert PASSWORD not in password_hash
        assert is_active is True
        assert last_login_at is not None
    finally:
        asyncio.run(_delete_users([email]))


def test_login_with_wrong_password_returns_401() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            response = client.post(
                "/auth/login",
                json={"email": email, "password": WRONG_PASSWORD},
            )

        assert response.status_code == 401, response.text
        body = response.json()
        assert body["code"] == "unauthorized"
        assert body["detail"] == "Неверный email или пароль"
    finally:
        asyncio.run(_delete_users([email]))


def test_login_with_unknown_email_returns_401() -> None:
    with _client() as client:
        response = client.post(
            "/auth/login",
            json={"email": _email(), "password": PASSWORD},
        )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


def test_login_of_inactive_user_returns_403() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
        asyncio.run(_set_active(email, active=False))
        with _client() as client:
            response = client.post(
                "/auth/login",
                json={"email": email, "password": PASSWORD},
            )

        assert response.status_code == 403, response.text
        assert response.json()["code"] == "forbidden"
    finally:
        asyncio.run(_delete_users([email]))


def test_login_timing_does_not_reveal_email_existence() -> None:
    """KI-043: время 401 не зависит от того, зарегистрирован ли email.

    Порог намеренно широкий (медианы по 7 запросам отличаются не более чем в
    2 раза, обе медленнее 5 мс), чтобы тест не был флаки на загруженной
    машине: Argon2id с параметрами по умолчанию занимает десятки мс.
    """
    existing_email = _email()
    samples: dict[str, list[float]] = {"existing": [], "unknown": []}
    try:
        with _client() as client:
            _register(client, existing_email)
            # Прогрев: первый Argon2 в пуле потоков и соединения БД.
            for warmup_email in (existing_email, _email()):
                client.post(
                    "/auth/login",
                    json={"email": warmup_email, "password": WRONG_PASSWORD},
                )
            for _ in range(7):
                for kind, email in (
                    ("existing", existing_email),
                    ("unknown", _email()),
                ):
                    started = time.perf_counter()
                    response = client.post(
                        "/auth/login",
                        json={"email": email, "password": WRONG_PASSWORD},
                    )
                    elapsed = time.perf_counter() - started
                    samples[kind].append(elapsed)
                    assert response.status_code == 401, response.text
    finally:
        asyncio.run(_delete_users([existing_email]))

    median_existing = statistics.median(samples["existing"])
    median_unknown = statistics.median(samples["unknown"])
    # Обе ветки реально считают Argon2id: 5 мс — заведомо ниже его стоимости.
    assert median_existing > 0.005, f"слишком быстро: {median_existing:.4f} с"
    assert median_unknown > 0.005, f"слишком быстро: {median_unknown:.4f} с"
    ratio = max(median_existing, median_unknown) / min(
        median_existing, median_unknown
    )
    assert ratio <= 2.0, (
        "время входа различает существование email: "
        f"existing={median_existing:.4f} с, unknown={median_unknown:.4f} с, "
        f"ratio={ratio:.2f}"
    )


def test_login_migrates_legacy_bcrypt_hash_to_argon2id() -> None:
    """KI-036: вход по bcrypt-хешу перехеширует его в Argon2id в той же транзакции."""
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
        asyncio.run(_set_password_hash(email, LEGACY_BCRYPT_HASH))
        stored_before, _, _ = asyncio.run(_read_user(email))
        assert stored_before == LEGACY_BCRYPT_HASH

        with _client() as client:
            tokens = _login(client, email)

        assert tokens["access_token"]
        password_hash, _, _ = asyncio.run(_read_user(email))
        assert password_hash is not None
        assert password_hash.startswith("$argon2id$")
        assert password_hash != LEGACY_BCRYPT_HASH
        assert verify_password(PASSWORD, password_hash) is True
    finally:
        asyncio.run(_delete_users([email]))


# --- Профиль --------------------------------------------------------------


def test_me_returns_profile_for_access_token() -> None:
    email = _email()
    try:
        with _client() as client:
            profile = _register(client, email)
            tokens = _login(client, email)
            response = client.get(
                "/auth/me",
                headers=_auth_headers(tokens["access_token"]),
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == PROFILE_KEYS
        assert body["id"] == profile["id"]
        assert body["email"] == email
        assert body["role"] == "user"
        assert body["is_active"] is True
    finally:
        asyncio.run(_delete_users([email]))


def test_me_rejects_missing_invalid_and_refresh_tokens() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            _login(client, email)
            refresh_token = client.cookies.get(COOKIE_NAME)
            assert refresh_token
            missing = client.get("/auth/me")
            garbage = client.get(
                "/auth/me", headers=_auth_headers("not-a-jwt")
            )
            with_refresh = client.get(
                "/auth/me", headers=_auth_headers(refresh_token)
            )
            wrong_scheme = client.get(
                "/auth/me", headers={"Authorization": refresh_token}
            )

        for response in (missing, garbage, with_refresh, wrong_scheme):
            assert response.status_code == 401, response.text
            assert response.json()["code"] == "unauthorized"
    finally:
        asyncio.run(_delete_users([email]))


# --- Обновление токенов ---------------------------------------------------


def test_refresh_by_cookie_rotates_cookie_and_returns_access() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            first = _login(client, email)
            first_cookie = client.cookies.get(COOKIE_NAME)
            assert first_cookie

            response = client.post("/auth/refresh")
            assert response.status_code == 200, response.text
            rotated = response.json()
            assert set(rotated) == ACCESS_RESPONSE_KEYS
            assert "refresh_token" not in rotated
            assert rotated["access_token"] != first["access_token"]
            assert rotated["token_type"] == "bearer"
            assert rotated["expires_in"] == ACCESS_TTL_S
            assert rotated["user"]["email"] == email

            # Ротация: cookie заменилась новым refresh-токеном.
            rotated_cookie = _assert_refresh_cookie(response)
            assert rotated_cookie != first_cookie
            assert client.cookies.get(COOKIE_NAME) == rotated_cookie

            # Новый access пускает в профиль...
            me = client.get(
                "/auth/me",
                headers=_auth_headers(rotated["access_token"]),
            )
            # ...а новый refresh можно ротировать ещё раз.
            again = client.post("/auth/refresh")

        assert me.status_code == 200, me.text
        assert me.json()["email"] == email
        assert again.status_code == 200, again.text
        assert again.json()["access_token"] != rotated["access_token"]
    finally:
        asyncio.run(_delete_users([email]))


def test_refresh_rotation_revokes_previous_token() -> None:
    """KI-037: старый refresh после ротации не принимается (защита от replay)."""
    email = _email()
    jtis: list[str] = []
    try:
        with _client() as client:
            _register(client, email)
            _login(client, email)
            old_cookie = client.cookies.get(COOKIE_NAME)
            assert old_cookie
            jtis.append(_jti(old_cookie))

            first = client.post("/auth/refresh")
            assert first.status_code == 200, first.text
            new_cookie = _assert_refresh_cookie(first)
            jtis.append(_jti(new_cookie))
            assert new_cookie != old_cookie

            # Replay старого refresh: cookie в банке уже новая, поэтому
            # имитируем «украденный токен» — без cookie, только тело.
            client.cookies.delete(COOKIE_NAME)
            replay = client.post(
                "/auth/refresh", json={"refresh_token": old_cookie}
            )
            assert replay.status_code == 401, replay.text
            assert replay.json()["code"] == "unauthorized"

            # Новый refresh при этом продолжает работать.
            client.cookies.set(COOKIE_NAME, new_cookie)
            second = client.post("/auth/refresh")
            assert second.status_code == 200, second.text
    finally:
        asyncio.run(_delete_users([email]))
        asyncio.run(_delete_denylist_keys(jtis))


def test_refresh_accepts_body_fallback_for_non_browser_clients() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            _login(client, email)
            refresh_token = client.cookies.get(COOKIE_NAME)
            assert refresh_token
            # Имитируем curl: cookie-джар пуст, токен — в теле JSON.
            client.cookies.clear()

            response = client.post(
                "/auth/refresh", json={"refresh_token": refresh_token}
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["access_token"]
        assert "refresh_token" not in body
        _assert_refresh_cookie(response)
    finally:
        asyncio.run(_delete_users([email]))


def test_refresh_without_token_returns_401() -> None:
    with _client() as client:
        response = client.post("/auth/refresh")

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


def test_refresh_rejects_malformed_cookie() -> None:
    with _client() as client:
        response = client.post(
            "/auth/refresh",
            headers={"Cookie": f"{COOKIE_NAME}=not-a-jwt"},
        )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


def test_refresh_rejects_access_token() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            tokens = _login(client, email)
            client.cookies.clear()
            response = client.post(
                "/auth/refresh",
                json={"refresh_token": tokens["access_token"]},
            )

        assert response.status_code == 401, response.text
        assert response.json()["code"] == "unauthorized"
    finally:
        asyncio.run(_delete_users([email]))


# --- Logout и denylist ----------------------------------------------------


@requires_redis
def test_logout_revokes_refresh_cookie_and_reuse_returns_401() -> None:
    email = _email()
    jtis: list[str] = []
    try:
        with _client() as client:
            _register(client, email)
            _login(client, email)
            refresh_token = client.cookies.get(COOKIE_NAME)
            assert refresh_token
            jtis.append(_jti(refresh_token))

            response = client.post("/auth/logout")
            assert response.status_code == 204, response.text
            assert response.content == b""
            _assert_cleared_cookie(response)
            assert client.cookies.get(COOKIE_NAME) is None

            # Повторное использование отозванного refresh — 401 (denylist).
            reuse = client.post(
                "/auth/refresh", json={"refresh_token": refresh_token}
            )

        assert reuse.status_code == 401, reuse.text
        assert reuse.json()["code"] == "unauthorized"
    finally:
        asyncio.run(_delete_denylist_keys(jtis))
        asyncio.run(_delete_users([email]))


@requires_redis
def test_logout_accepts_body_refresh_token() -> None:
    email = _email()
    jtis: list[str] = []
    try:
        with _client() as client:
            _register(client, email)
            _login(client, email)
            refresh_token = client.cookies.get(COOKIE_NAME)
            assert refresh_token
            jtis.append(_jti(refresh_token))
            client.cookies.clear()

            response = client.post(
                "/auth/logout", json={"refresh_token": refresh_token}
            )
            assert response.status_code == 204, response.text
            _assert_cleared_cookie(response)

            reuse = client.post(
                "/auth/refresh", json={"refresh_token": refresh_token}
            )

        assert reuse.status_code == 401, reuse.text
    finally:
        asyncio.run(_delete_denylist_keys(jtis))
        asyncio.run(_delete_users([email]))


def test_logout_without_token_clears_cookie_and_returns_204() -> None:
    with _client() as client:
        response = client.post("/auth/logout")

    assert response.status_code == 204, response.text
    _assert_cleared_cookie(response)


def test_logout_with_broken_token_returns_204_and_clears_cookie() -> None:
    with _client() as client:
        response = client.post(
            "/auth/logout",
            headers={"Cookie": f"{COOKIE_NAME}=not-a-jwt"},
        )

    assert response.status_code == 204, response.text
    _assert_cleared_cookie(response)


class _BrokenRedis:
    """Заглушка Redis-клиента, у которого «лежит» соединение."""

    async def setex(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("redis down")

    async def exists(self, *_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("redis down")


def test_logout_is_fail_open_when_redis_unavailable() -> None:
    """KI-037: недоступный Redis не ломает logout (204 + очистка cookie).

    Осознанный риск fail-open: пока Redis лежит, denylist не работает и
    отозванный refresh можно использовать до истечения срока.
    """
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            _login(client, email)
            refresh_token = client.cookies.get(COOKIE_NAME)
            assert refresh_token
            client.app.state.redis = _BrokenRedis()  # type: ignore[attr-defined]

            response = client.post("/auth/logout")
            assert response.status_code == 204, response.text
            _assert_cleared_cookie(response)

            # Риск задокументирован: при упавшем Redis проверка denylist
            # fail-open, поэтому токен ещё рабочий.
            still_valid = client.post(
                "/auth/refresh", json={"refresh_token": refresh_token}
            )

        assert still_valid.status_code == 200, still_valid.text
    finally:
        asyncio.run(_delete_users([email]))


# --- CORS и rate limiting -------------------------------------------------


def test_cors_preflight_allows_credentials_for_auth_login() -> None:
    """KI-037: cookie-аутентификация требует allow_credentials=True в CORS."""
    with _client() as client:
        response = client.options(
            "/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200, response.text
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]


def _redis_url_for_db(db: int) -> str:
    """URL Redis с отдельной БД — чтобы счётчики лимитера не пересекались."""
    base = get_settings().redis_url.rsplit("/", 1)[0]
    return f"{base}/{db}"


async def _flush_redis_db(url: str) -> None:
    client = aioredis.from_url(url, decode_responses=True)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


@requires_redis
def test_login_rate_limit_still_applies() -> None:
    """Rate limiting `/auth/login` (T-009) не сломан cookie-рефакторингом."""
    redis_url = _redis_url_for_db(15)
    asyncio.run(_flush_redis_db(redis_url))
    try:
        with _client(
            redis_url=redis_url,
            rate_limit_enabled=True,
            rate_limit_requests=2,
        ) as client:
            responses = [
                client.post(
                    "/auth/login",
                    json={"email": _email(), "password": PASSWORD},
                )
                for _ in range(3)
            ]

        assert responses[0].status_code == 401, responses[0].text
        assert responses[1].status_code == 401, responses[1].text
        assert responses[2].status_code == 429, responses[2].text
        assert responses[2].json()["code"] == "rate_limited"
        # Окно лимита скользящее по секундам: между запросами может пройти
        # часть секунды, поэтому Retry-After = 59..60, а не строго 60.
        assert 1 <= int(responses[2].headers["retry-after"]) <= 60
    finally:
        asyncio.run(_flush_redis_db(redis_url))
