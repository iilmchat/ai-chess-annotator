"""JSONL-экспорт датасета правок в админке (T-034b).

Проверяем `GET /v1/admin/exports/edits.jsonl`:

* авторизация: JWT admin/auditor — 200; role user — 403; без кредов — 401;
  cookie-сессия `admin_session` (кнопка из админки) — 200;
* NDJSON-стрим с `Content-Disposition`, X-Trace-Id в заголовках;
* фильтры `date_from/date_to`, `level`, `human_edited`, `schema_version`;
* v2: `user` — пакет фактов (`# Партия`), `assistant` — `edited_text`,
  `metadata`/`schema_version=2`; v1: `user == original_text`;
* страница `/admin/exports/dataset` с формой (admin и auditor);
* аудит `dataset.export` (KI-050): фильтры, число выгруженных строк и
  актор (JWT/сессия) пишутся в `audit_logs` после завершения стрима.

Данные (Game/Move/Commentary/Edit) сидятся напрямую ORM и удаляются после
теста. Требуется PostgreSQL; без него модуль скипается.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.tokens import create_access_token
from app.config import Settings, get_settings
from app.db.models import (
    AdminRole,
    AuditLog,
    Commentary,
    Edit,
    Game,
    Level,
    Move,
    User,
)
from app.ingestion import parse_pgn
from app.main import create_app

ADMIN_TOKEN = "test-admin-token"
ADMIN_PASSWORD = "test-admin-password"
ROLES = ("admin", "auditor", "user")

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"
ORIGINAL_CLUB = "Клубный черновик комментария."
EDITED_CLUB = "Исправленный клубный комментарий после правки."
ORIGINAL_GM = "Гроссмейстерский черновик."
EDITED_GM = "Исправленный гроссмейстерский комментарий."
CLUB_DATE = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
GM_DATE = datetime(2020, 5, 5, 9, 0, tzinfo=UTC)


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


def _db_url() -> str:
    return get_settings().database_url


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_edits() -> dict[str, int]:
    """Партии с анализом + комментарии + правки (club 2026 и gm 2020)."""
    parsed = parse_pgn(PGN)
    factory = _factory()
    ids: dict[str, int] = {}
    async with factory() as session:
        for key, level, human_edited, created_at, original, edited in (
            (
                "club",
                Level.club,
                False,
                CLUB_DATE,
                ORIGINAL_CLUB,
                EDITED_CLUB,
            ),
            (
                "gm",
                Level.gm,
                True,
                GM_DATE,
                ORIGINAL_GM,
                EDITED_GM,
            ),
        ):
            game = Game(pgn=PGN, eco_code=parsed.eco_code)
            session.add(game)
            await session.flush()
            for move in parsed.moves:
                session.add(
                    Move(
                        game_id=game.id,
                        ply=move.ply,
                        san=move.san,
                        fen=move.fen,
                        eval_cp=0,
                        best_move_san=move.san,
                        delta_cp=0,
                        classification="good",
                        engine_depth=10,
                    )
                )
            commentary = Commentary(
                game_id=game.id,
                level=level,
                text=original,
                model="test-model",
                validated=True,
                human_edited=human_edited,
            )
            session.add(commentary)
            await session.flush()
            edit = Edit(
                commentary_id=commentary.id,
                original_text=original,
                edited_text=edited,
                created_at=created_at,
            )
            session.add(edit)
            await session.flush()
            ids[key] = edit.id
            ids[f"{key}_game"] = game.id
        await session.commit()
        # Детерминированно сортируем экспорт по id: club-правка раньше gm.
        assert ids["club"] < ids["gm"]
    return ids


async def _delete_seed(ids: dict[str, int]) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            for key in ("club_game", "gm_game"):
                game_id = ids.get(key)
                if game_id is not None:
                    await connection.execute(
                        Game.__table__.delete().where(Game.id == game_id)
                    )
    finally:
        await engine.dispose()


async def _insert_users(emails: dict[str, str]) -> dict[str, User]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    users: dict[str, User] = {}
    try:
        async with factory() as session:
            for role, email in emails.items():
                user = User(email=email, role=AdminRole(role), is_active=True)
                session.add(user)
                await session.flush()
                users[role] = user
            await session.commit()
    finally:
        await engine.dispose()
    return users


async def _delete_users(emails: list[str]) -> None:
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
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
        }
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def seeded(client: TestClient) -> Iterator[dict[str, int]]:
    ids = asyncio.run(_seed_edits())
    try:
        yield ids
    finally:
        asyncio.run(_delete_seed(ids))


@pytest.fixture()
def users(client: TestClient) -> Iterator[dict[str, str]]:
    emails = {
        role: f"export-{role}-{uuid4().hex}@example.com" for role in ROLES
    }
    inserted = asyncio.run(_insert_users(emails))
    assert set(inserted) == set(ROLES)
    try:
        yield emails
    finally:
        asyncio.run(_delete_users(list(emails.values())))


def _bearer(settings: Settings, users: dict[str, str], role: str) -> dict[str, str]:
    async def load() -> User:
        engine = create_async_engine(_db_url(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                user = await session.scalar(
                    select(User).where(User.email == users[role])
                )
                assert user is not None
                return user
        finally:
            await engine.dispose()

    user = asyncio.run(load())
    token = create_access_token(user, settings)
    return {"Authorization": f"Bearer {token}"}


def _parse_ndjson(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in response.text.splitlines()
        if line.strip()
    ]


def _login_admin(client: TestClient) -> None:
    """Парольная форма /admin/login → cookie admin_session в клиенте."""
    page = client.get("/admin/login")
    assert page.status_code == 200
    match = re.search(r'name="csrftoken" value="([^"]+)"', page.text)
    assert match is not None, "форма логина должна содержать CSRF-токен"
    response = client.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "csrftoken": match.group(1),
        },
    )
    assert response.status_code == 303, response.text


# --- Авторизация ----------------------------------------------------------


def test_export_requires_credentials(
    client: TestClient, seeded: dict[str, int]
) -> None:
    response = client.get("/v1/admin/exports/edits.jsonl")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_export_rejects_user_role(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    response = client.get(
        "/v1/admin/exports/edits.jsonl",
        headers=_bearer(settings, users, "user"),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_export_allows_auditor_jwt(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    response = client.get(
        "/v1/admin/exports/edits.jsonl?level=club",
        headers=_bearer(settings, users, "auditor"),
    )
    assert response.status_code == 200, response.text


def test_export_allows_admin_session_cookie(
    client: TestClient, seeded: dict[str, int]
) -> None:
    """"Кнопка» из админки: cookie admin_session без Bearer-токена."""
    _login_admin(client)
    response = client.get("/v1/admin/exports/edits.jsonl?level=club")
    assert response.status_code == 200, response.text
    assert "admin_session" in client.cookies


# --- v2/v1 и фильтры ------------------------------------------------------


def test_export_v2_streams_fact_sheet_dataset(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    response = client.get(
        "/v1/admin/exports/edits.jsonl?level=club",
        headers=_bearer(settings, users, "admin"),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/x-ndjson"
    )
    assert "attachment" in response.headers["content-disposition"]
    assert "edits.jsonl" in response.headers["content-disposition"]
    assert "X-Trace-Id" in response.headers

    lines = _parse_ndjson(response)
    assert len(lines) == 1
    record = lines[0]
    assert record["schema_version"] == 2
    assert record["metadata"]["game_id"] == seeded["club_game"]
    messages = record["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert messages[1]["content"].startswith("# Партия")
    assert "# Ключевые моменты" in messages[1]["content"]
    assert messages[2]["content"] == EDITED_CLUB
    assert record["metadata"]["fact_sheet_chars"] == len(messages[1]["content"])


def test_export_v1_uses_original_text(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    response = client.get(
        "/v1/admin/exports/edits.jsonl?level=gm&schema_version=1",
        headers=_bearer(settings, users, "admin"),
    )
    assert response.status_code == 200, response.text
    lines = _parse_ndjson(response)
    assert len(lines) == 1
    assert lines[0]["schema_version"] == 1
    assert "metadata" not in lines[0]
    assert lines[0]["messages"][1]["content"] == ORIGINAL_GM
    assert lines[0]["messages"][2]["content"] == EDITED_GM


def test_export_filters_by_human_edited(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    headers = _bearer(settings, users, "admin")
    humans = client.get(
        "/v1/admin/exports/edits.jsonl?human_edited=true&schema_version=1",
        headers=headers,
    )
    assert humans.status_code == 200
    human_lines = _parse_ndjson(humans)
    assert len(human_lines) == 1
    assert human_lines[0]["messages"][2]["content"] == EDITED_GM

    not_humans = client.get(
        "/v1/admin/exports/edits.jsonl?human_edited=false&schema_version=1",
        headers=headers,
    )
    assert not_humans.status_code == 200
    not_human_lines = _parse_ndjson(not_humans)
    assert len(not_human_lines) == 1
    assert not_human_lines[0]["messages"][2]["content"] == EDITED_CLUB


def test_export_filters_by_date(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    headers = _bearer(settings, users, "admin")
    recent = client.get(
        "/v1/admin/exports/edits.jsonl?date_from=2026-01-01&schema_version=1",
        headers=headers,
    )
    assert recent.status_code == 200
    recent_lines = _parse_ndjson(recent)
    assert len(recent_lines) == 1
    assert recent_lines[0]["messages"][2]["content"] == EDITED_CLUB

    old = client.get(
        "/v1/admin/exports/edits.jsonl?date_to=2025-12-31&schema_version=1",
        headers=headers,
    )
    assert old.status_code == 200
    old_lines = _parse_ndjson(old)
    assert len(old_lines) == 1
    assert old_lines[0]["messages"][2]["content"] == EDITED_GM


def test_export_empty_result_is_200(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    response = client.get(
        "/v1/admin/exports/edits.jsonl?date_from=2000-01-01&date_to=2000-01-02",
        headers=_bearer(settings, users, "admin"),
    )
    assert response.status_code == 200
    assert response.text == ""


def test_export_invalid_date_is_422(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
) -> None:
    response = client.get(
        "/v1/admin/exports/edits.jsonl?date_from=not-a-date",
        headers=_bearer(settings, users, "admin"),
    )
    assert response.status_code == 422


# --- Страница экспорта в админке -----------------------------------------


def test_admin_export_page_is_available(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
) -> None:
    for role in ("admin", "auditor"):
        response = client.get(
            "/admin/exports/dataset",
            headers=_bearer(settings, users, role),
        )
        assert response.status_code == 200, (role, response.text)
        assert "dataset-export-form" in response.text
        assert "/v1/admin/exports/edits.jsonl" in response.text
        # KI-050: форма фоновой выгрузки; auditor — read-only.
        assert "/v1/admin/exports/jobs" in response.text
        if role == "admin":
            assert "Создать экспорт" in response.text
        else:
            assert "Роль auditor" in response.text


# --- Аудит выгрузки (KI-050, итерация 29) ---------------------------------


async def _max_audit_id() -> int:
    """Максимальный id в audit_logs до запроса (аудит append-only)."""
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            value = await session.scalar(select(func.max(AuditLog.id)))
            return int(value or 0)
    finally:
        await engine.dispose()


async def _export_audits_after(audit_id: int) -> list[AuditLog]:
    """Записи `dataset.export`, появившиеся после `audit_id`."""
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = await session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action == "dataset.export",
                    AuditLog.id > audit_id,
                )
                .order_by(AuditLog.id)
            )
            return list(rows)
    finally:
        await engine.dispose()


def _await_export_audit(audit_id: int, timeout_s: float = 5.0) -> AuditLog:
    """Дождаться аудита выгрузки (пишется после завершения генератора)."""
    deadline = time.monotonic() + timeout_s
    while True:
        rows = asyncio.run(_export_audits_after(audit_id))
        if rows:
            return rows[0]
        if time.monotonic() >= deadline:
            raise AssertionError(
                "audit_logs.action=dataset.export не появился после выгрузки"
            )
        time.sleep(0.05)


def test_export_writes_audit_record(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    """Фильтры и число фактически выгруженных строк попадают в аудит."""
    before = asyncio.run(_max_audit_id())
    response = client.get(
        "/v1/admin/exports/edits.jsonl"
        "?level=club&human_edited=false&schema_version=1",
        headers=_bearer(settings, users, "admin"),
    )
    assert response.status_code == 200, response.text
    assert len(_parse_ndjson(response)) == 1

    audit = _await_export_audit(before)
    assert audit.action == "dataset.export"
    assert audit.target_id == "edits.jsonl"
    assert audit.actor.startswith("jwt:")
    details = audit.details
    assert details["level"] == "club"
    assert details["human_edited"] is False
    assert details["schema_version"] == 1
    assert details["date_from"] is None
    assert details["date_to"] is None
    assert details["rows"] == 1
    assert details["completed"] is True
    assert isinstance(details["trace_id"], str)
    assert details["trace_id"]


def test_export_empty_result_audited_with_zero_rows(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    before = asyncio.run(_max_audit_id())
    response = client.get(
        "/v1/admin/exports/edits.jsonl"
        "?date_from=2000-01-01&date_to=2000-01-02",
        headers=_bearer(settings, users, "admin"),
    )
    assert response.status_code == 200
    assert response.text == ""

    audit = _await_export_audit(before)
    assert audit.details["rows"] == 0
    assert audit.details["completed"] is True
    assert audit.details["date_from"] == "2000-01-01"
    assert audit.details["date_to"] == "2000-01-02"


def test_export_audit_actor_from_admin_cookie_session(
    client: TestClient, seeded: dict[str, int]
) -> None:
    """Кнопка из админки: актор — парольная сессия, а не JWT."""
    _login_admin(client)
    before = asyncio.run(_max_audit_id())
    response = client.get("/v1/admin/exports/edits.jsonl?level=club")
    assert response.status_code == 200, response.text

    audit = _await_export_audit(before)
    assert audit.actor == "session:admin"
    assert audit.details["level"] == "club"
    assert audit.details["human_edited"] is None
    assert audit.details["schema_version"] == 2
    assert audit.details["rows"] == 1


def test_export_audit_for_auditor_jwt(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
) -> None:
    before = asyncio.run(_max_audit_id())
    response = client.get(
        "/v1/admin/exports/edits.jsonl?level=gm&schema_version=1",
        headers=_bearer(settings, users, "auditor"),
    )
    assert response.status_code == 200, response.text

    audit = _await_export_audit(before)
    assert audit.actor.startswith("jwt:")
    assert audit.details["level"] == "gm"
    assert audit.details["rows"] == 1
