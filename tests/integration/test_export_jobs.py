"""Фоновая выгрузка датасета: `export_jobs` + download (KI-050).

Проверяем:

* `POST /v1/admin/exports/jobs` — 202, джоба создаётся и выполняется
  BackgroundTasks; файл лежит в `EXPORT_DIR` (вне web-root), статус
  ``done``, `rows_exported`/`sha256`/`file_size` заполнены;
* `GET /jobs/{id}` — статус, `download_url`, 404 для несуществующей;
* `GET /jobs/{id}/download` — NDJSON, `Content-Disposition`, sha256;
  queued/failed → 409, «файл вне EXPORT_DIR» → 410;
* RBAC: auditor может читать/скачивать, но не создавать джобу (403);
  аноним — 401, роль user — 403;
* пустая выборка — валидный пустой файл, `rows_exported=0`;
* аудит `dataset.export` содержит `job_id`, `background=true`, фильтры.

Требуется PostgreSQL; без него модуль скипается.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from arq.connections import RedisSettings
from arq.worker import Worker
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.tokens import create_access_token
from app.config import Settings, get_settings
from app.dataset.jobs import recover_stale_export_jobs
from app.db.models import (
    AdminRole,
    AuditLog,
    Commentary,
    Edit,
    ExportJob,
    Game,
    Level,
    Move,
    User,
)
from app.ingestion import parse_pgn
from app.main import create_app
from app.worker import WorkerSettings, run_export_job

PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"
COMMENTARY_TEXT = "Комментарий для выгрузки."
EDITED_TEXT = "Правка для выгрузки."
ROLES = ("admin", "auditor", "user")


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


def _redis_available() -> bool:
    async def ping() -> None:
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            get_settings().redis_url
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


pytestmark = [
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен"),
    pytest.mark.skipif(not _redis_available(), reason="Redis/arq недоступен"),
]


def _db_url() -> str:
    return get_settings().database_url


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_edit() -> dict[str, int]:
    """Партия + ходы + комментарий + правка (одна запись v2)."""
    parsed = parse_pgn(PGN)
    factory = _factory()
    async with factory() as session:
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
            level=Level.club,
            text=COMMENTARY_TEXT,
            model="test-model",
            validated=True,
        )
        session.add(commentary)
        await session.flush()
        edit = Edit(
            commentary_id=commentary.id,
            original_text=COMMENTARY_TEXT,
            edited_text=EDITED_TEXT,
            # Фиксированная дата: тест не должен зависеть от «сегодня» и от
            # посторонних правок в общей dev-БД (изоляция выборки).
            created_at=datetime(2020, 5, 5, 9, 0, tzinfo=UTC),
        )
        session.add(edit)
        await session.commit()
        return {"game_id": game.id, "commentary_id": commentary.id}


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                Game.__table__.delete().where(Game.id == game_id)
            )
    finally:
        await engine.dispose()


async def _insert_users(emails: dict[str, str]) -> dict[str, int]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, int] = {}
    try:
        async with factory() as session:
            for role, email in emails.items():
                user = User(
                    email=email, role=AdminRole(role), is_active=True
                )
                session.add(user)
                await session.flush()
                ids[role] = user.id
            await session.commit()
    finally:
        await engine.dispose()
    return ids


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


async def _max_job_id() -> int:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            value = await session.scalar(select(func.max(ExportJob.id)))
            return int(value or 0)
    finally:
        await engine.dispose()


async def _delete_jobs_after(job_id: int) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                ExportJob.__table__.delete().where(ExportJob.id > job_id)
            )
    finally:
        await engine.dispose()


async def _get_job(job_id: int) -> ExportJob | None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await session.get(ExportJob, job_id)
    finally:
        await engine.dispose()


async def _insert_job(**values: Any) -> int:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = ExportJob(**values)
            session.add(job)
            await session.commit()
            return job.id
    finally:
        await engine.dispose()


async def _max_audit_id() -> int:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            value = await session.scalar(select(func.max(AuditLog.id)))
            return int(value or 0)
    finally:
        await engine.dispose()


async def _export_audits_after(audit_id: int) -> list[AuditLog]:
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


def _await_job_status(job_id: int, status: str, timeout_s: float = 5.0) -> ExportJob:
    deadline = time.monotonic() + timeout_s
    while True:
        job = asyncio.run(_get_job(job_id))
        if job is not None and job.status == status:
            return job
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"джоба {job_id} не достигла статуса {status} "
                f"(текущий: {job.status if job else None})"
            )
        time.sleep(0.05)


async def _run_worker(settings: Settings) -> None:
    """Burst-воркер arq: обработать очередь и завершиться (KI-053)."""
    worker = Worker(
        [run_export_job],
        redis_settings=RedisSettings.from_dsn(settings.redis_url),
        queue_name=settings.arq_queue_name,
        burst=True,
        handle_signals=False,
        ctx={"settings": settings},
        max_tries=1,
        keep_result=60,
    )
    await worker.async_run()


def _drain_queue(client: TestClient, settings: Settings) -> None:
    """Обработать очередь в event loop приложения (TestClient portal).

    Нельзя запускать `asyncio.run`: глобальный `SessionLocal`/asyncpg-пул
    уже привязан к loop-у TestClient, и воркер в новом loop получил бы
    «Future attached to a different loop».
    """
    portal = client.portal
    assert portal is not None
    portal.call(_run_worker, settings)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return get_settings().model_copy(
        update={
            "admin_api_key": "test-admin-token",
            "admin_password": "test-admin-password",
            "admin_username": "admin",
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
            "engine_cache_enabled": False,
            "test_auth_bypass": False,
            "export_dir": str(tmp_path / "exports"),
            # Уникальная очередь: тесты не видят чужие задачи в общем Redis.
            "arq_queue_name": f"arq:test:{uuid4().hex}",
        }
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), follow_redirects=False) as c:
        yield c


@pytest.fixture()
def seeded(client: TestClient) -> Iterator[dict[str, int]]:
    ids = asyncio.run(_seed_edit())
    try:
        yield ids
    finally:
        asyncio.run(_delete_game(ids["game_id"]))


@pytest.fixture()
def users(client: TestClient) -> Iterator[dict[str, str]]:
    emails = {
        role: f"export-job-{role}-{uuid4().hex}@example.com"
        for role in ROLES
    }
    asyncio.run(_insert_users(emails))
    try:
        yield emails
    finally:
        asyncio.run(_delete_users(list(emails.values())))


@pytest.fixture()
def jobs_cleanup() -> Iterator[int]:
    before = asyncio.run(_max_job_id())
    try:
        yield before
    finally:
        asyncio.run(_delete_jobs_after(before))


def _bearer(
    settings: Settings, users: dict[str, str], role: str
) -> dict[str, str]:
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
    return {
        "Authorization": f"Bearer {create_access_token(user, settings)}"
    }


def _create_job(
    client: TestClient, headers: dict[str, str], **payload: Any
) -> Any:
    return client.post(
        "/v1/admin/exports/jobs",
        json={"schema_version": 2, **payload},
        headers=headers,
    )


def _login_admin(client: TestClient) -> None:
    """Парольная форма /admin/login → cookie admin_session (кнопка в UI)."""
    page = client.get("/admin/login")
    assert page.status_code == 200
    match = re.search(r'name="csrftoken" value="([^"]+)"', page.text)
    assert match is not None
    response = client.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": "test-admin-password",
            "csrftoken": match.group(1),
        },
    )
    assert response.status_code == 303, response.text


def test_create_job_runs_and_downloads(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
    jobs_cleanup: int,
) -> None:
    headers = _bearer(settings, users, "admin")
    response = _create_job(client, headers, level="club")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["download_url"] is None
    job_id = body["id"]

    _drain_queue(client, settings)
    job = _await_job_status(job_id, "done")
    assert job.rows_exported == 1
    assert job.error is None
    assert job.finished_at is not None
    assert job.file_path is not None
    path = Path(job.file_path)
    assert path.is_file()
    assert path.parent == Path(settings.export_dir).resolve()
    raw = path.read_bytes()
    assert job.file_size == len(raw)
    assert job.sha256 == hashlib.sha256(raw).hexdigest()
    record = json.loads(raw.decode("utf-8").strip())
    assert record["schema_version"] == 2
    assert record["messages"][2]["content"] == EDITED_TEXT

    status = client.get(
        f"/v1/admin/exports/jobs/{job_id}", headers=headers
    )
    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "done"
    assert payload["download_url"].endswith(
        f"/v1/admin/exports/jobs/{job_id}/download"
    )
    assert payload["sha256"] == job.sha256

    download = client.get(payload["download_url"], headers=headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith(
        "application/x-ndjson"
    )
    disposition = download.headers["content-disposition"]
    assert "attachment" in disposition
    assert f"edits-{job_id}.jsonl" in disposition
    assert download.headers["x-export-sha256"] == job.sha256
    assert download.content == raw


def test_create_job_empty_dataset_produces_empty_file(
    client: TestClient, settings: Settings, users: dict[str, str],
    jobs_cleanup: int,
) -> None:
    headers = _bearer(settings, users, "admin")
    response = _create_job(
        client,
        headers,
        date_from="2000-01-01",
        date_to="2000-01-02",
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    _drain_queue(client, settings)
    job = _await_job_status(job_id, "done")
    assert job.rows_exported == 0
    assert job.file_size == 0
    download = client.get(
        f"/v1/admin/exports/jobs/{job_id}/download", headers=headers
    )
    assert download.status_code == 200
    assert download.content == b""


def test_job_list_contains_created_job(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
    jobs_cleanup: int,
) -> None:
    headers = _bearer(settings, users, "admin")
    job_id = _create_job(client, headers).json()["id"]
    _drain_queue(client, settings)
    _await_job_status(job_id, "done")
    response = client.get("/v1/admin/exports/jobs", headers=headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert job_id in ids


def test_auditor_cannot_create_but_can_download(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
    jobs_cleanup: int,
) -> None:
    admin_headers = _bearer(settings, users, "admin")
    job_id = _create_job(client, admin_headers).json()["id"]
    _drain_queue(client, settings)
    _await_job_status(job_id, "done")

    auditor_headers = _bearer(settings, users, "auditor")
    forbidden = _create_job(client, auditor_headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"

    listed = client.get(
        "/v1/admin/exports/jobs", headers=auditor_headers
    )
    assert listed.status_code == 200
    download = client.get(
        f"/v1/admin/exports/jobs/{job_id}/download",
        headers=auditor_headers,
    )
    assert download.status_code == 200


def test_jobs_require_admin_credentials(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    jobs_cleanup: int,
) -> None:
    anonymous = client.post("/v1/admin/exports/jobs", json={})
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "unauthorized"

    user_headers = _bearer(settings, users, "user")
    denied = _create_job(client, user_headers)
    assert denied.status_code == 403


def test_download_not_ready_and_missing(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    jobs_cleanup: int,
) -> None:
    headers = _bearer(settings, users, "admin")
    queued_id = asyncio.run(
        _insert_job(
            status="queued",
            actor="jwt:test@example.com",
            filters={},
        )
    )
    queued = client.get(
        f"/v1/admin/exports/jobs/{queued_id}/download", headers=headers
    )
    assert queued.status_code == 409
    assert queued.json()["code"] == "export_job_not_ready"

    failed_id = asyncio.run(
        _insert_job(
            status="failed",
            actor="jwt:test@example.com",
            filters={},
            error="boom",
            finished_at=datetime.now(UTC),
        )
    )
    failed = client.get(
        f"/v1/admin/exports/jobs/{failed_id}/download", headers=headers
    )
    assert failed.status_code == 409
    assert "boom" in failed.json()["detail"]

    missing = client.get(
        "/v1/admin/exports/jobs/999999999", headers=headers
    )
    assert missing.status_code == 404


def test_download_rejects_path_outside_export_dir(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    tmp_path: Path,
    jobs_cleanup: int,
) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"{}\n")
    job_id = asyncio.run(
        _insert_job(
            status="done",
            actor="jwt:test@example.com",
            filters={},
            file_path=str(outside),
            file_size=outside.stat().st_size,
            rows_exported=1,
            finished_at=datetime.now(UTC),
        )
    )
    headers = _bearer(settings, users, "admin")
    response = client.get(
        f"/v1/admin/exports/jobs/{job_id}/download", headers=headers
    )
    assert response.status_code == 410
    assert response.json()["code"] == "export_job_file_missing"


def test_job_writes_audit_record(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, int],
    jobs_cleanup: int,
) -> None:
    before = asyncio.run(_max_audit_id())
    job_id = _create_job(
        client, _bearer(settings, users, "admin"), level="club"
    ).json()["id"]
    _drain_queue(client, settings)
    _await_job_status(job_id, "done")

    deadline = time.monotonic() + 5.0
    audit: AuditLog | None = None
    while audit is None:
        rows = [
            row
            for row in asyncio.run(_export_audits_after(before))
            if row.details.get("job_id") == job_id
        ]
        if rows:
            audit = rows[0]
            break
        if time.monotonic() >= deadline:
            raise AssertionError("аудит dataset.export для джобы не найден")
        time.sleep(0.05)
    details = audit.details
    assert details["background"] is True
    assert details["job_id"] == job_id
    assert details["level"] == "club"
    assert details["rows"] == 1
    assert details["completed"] is True
    assert isinstance(details["trace_id"], str)
    assert details["trace_id"]
    assert audit.actor.startswith("jwt:")


def test_admin_cookie_session_can_create_and_download(
    client: TestClient,
    settings: Settings,
    seeded: dict[str, int],
    jobs_cleanup: int,
) -> None:
    """Кнопка в админке: cookie admin_session без Bearer-токена."""
    _login_admin(client)
    response = client.post(
        "/v1/admin/exports/jobs",
        json={
            "schema_version": 1,
            "date_from": "2020-05-05",
            "date_to": "2020-05-05",
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    _drain_queue(client, settings)
    job = _await_job_status(job_id, "done")
    assert job.rows_exported == 1

    download = client.get(f"/v1/admin/exports/jobs/{job_id}/download")
    assert download.status_code == 200
    record = json.loads(download.content.decode("utf-8").strip())
    assert record["schema_version"] == 1
    assert record["messages"][2]["content"] == EDITED_TEXT


# --- KI-053: arq-воркер и recovery ---------------------------------------


def test_worker_settings_registers_export_task() -> None:
    """arq CLI/Docker находит задачу и настройки в `worker.WorkerSettings`."""
    assert run_export_job in WorkerSettings.functions
    assert WorkerSettings.redis_settings is not None
    assert WorkerSettings.queue_name == get_settings().arq_queue_name
    assert WorkerSettings.on_startup is not None


def test_recover_stale_running_job_marks_failed(
    client: TestClient, settings: Settings, jobs_cleanup: int
) -> None:
    """Зависшая running-джоба старше 15 минут → failed (KI-053)."""
    started = datetime.now(UTC) - timedelta(minutes=20)
    job_id = asyncio.run(
        _insert_job(
            status="running",
            actor="jwt:stale@example.com",
            filters={},
            started_at=started,
        )
    )
    portal = client.portal
    assert portal is not None
    recovered = portal.call(recover_stale_export_jobs, settings)
    assert recovered >= 1
    job = _await_job_status(job_id, "failed")
    assert job.error is not None
    assert job.error.startswith("stale:")
    assert job.finished_at is not None


def test_queue_unavailable_marks_job_failed(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    jobs_cleanup: int,
) -> None:
    """Redis/arq недоступны: 503 + джоба failed, а не вечный queued."""
    client.app.state.arq_pool = None
    response = _create_job(client, _bearer(settings, users, "admin"))
    assert response.status_code == 503
    assert response.json()["code"] == "export_queue_unavailable"

    async def latest() -> ExportJob:
        engine = create_async_engine(_db_url(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                job = await session.scalar(
                    select(ExportJob).order_by(ExportJob.id.desc()).limit(1)
                )
                assert job is not None
                return job
        finally:
            await engine.dispose()

    job = asyncio.run(latest())
    assert job.status == "failed"
    assert job.error is not None

