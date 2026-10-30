"""Админ-панель: журнал `export_jobs` и фильтр по статусу (итерация 35).

Проверяем read-only view фоновых выгрузок датасета (KI-050):

* ``/admin/export-job/list`` доступен админу и показывает строки журнала;
* в списке есть status-фильтр: `EnumField` отдаёт в filter builder
  выпадающий список ``queued/running/done/failed``, а не свободный текст;
* HTTP-фильтр ``?filter=status__eq=failed`` отдаёт только упавшие джобы
  (акторы done/queued/running в выдаче отсутствуют);
* ``status__in=done,failed`` (multi-select того же фильтра) работает;
* битый ``filter`` — 400, а не 500.

Строки `export_jobs` сидятся напрямую ORM с уникальными акторами и
удаляются после теста. Требуется PostgreSQL; без него модуль скипается.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import ExportJob, ExportJobStatus
from app.main import create_app

ADMIN_TOKEN = "test-admin-token"
AUTH = {"X-Admin-Token": ADMIN_TOKEN}
#: Ключ view = slugify(имя ORM-модели) — как у остальных ModelView.
VIEW_PATH = "/admin/export-job/list"

#: Все статусы `ExportJobStatus` в порядке объявления enum.
STATUSES: tuple[str, ...] = tuple(status.value for status in ExportJobStatus)


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


@dataclass(frozen=True, slots=True)
class SeededJobs:
    """Акторы и id сидят-строк по статусам (уникальны в рамках теста)."""

    actors: dict[str, str]
    ids: dict[str, int]


def _unique_actors() -> dict[str, str]:
    """Уникальный актор на каждый статус: строки других тестов не мешают."""
    token = uuid4().hex
    return {
        status: f"admin-export-jobs-{status}-{token}@example.com"
        for status in STATUSES
    }


async def _seed_jobs(actors: dict[str, str]) -> dict[str, int]:
    """Вставить по одной джобе на каждый статус (новые id — сверху списка)."""
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, int] = {}
    try:
        async with factory() as session:
            for status in STATUSES:
                done = status == ExportJobStatus.done.value
                failed = status == ExportJobStatus.failed.value
                job = ExportJob(
                    status=status,
                    actor=actors[status],
                    filters={"level": "club", "schema_version": 2},
                    file_size=1024 if done else None,
                    sha256="a" * 64 if done else None,
                    rows_exported=7 if done else 0,
                    error="boom: LLM validation failed" if failed else None,
                )
                session.add(job)
                await session.flush()
                ids[status] = int(job.id)
            await session.commit()
    finally:
        await engine.dispose()
    return ids


async def _delete_jobs(ids: list[int]) -> None:
    if not ids:
        return
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(ExportJob).where(ExportJob.id.in_(ids))
            )
    finally:
        await engine.dispose()


def _filter_builder_fields(html: str) -> list[dict[str, Any]]:
    """Распарсить JSON-конфиг filter builder из list-страницы.

    starlette-admin 1.0.3 кладёт его в inline-скрипт
    ``initFilterBuilder({fields: [...], initialFilter: ...})``.
    """
    match = re.search(
        r"initFilterBuilder\(\{\s*fields:\s*(\[.*?\]),\s*initialFilter:",
        html,
        re.DOTALL,
    )
    assert match is not None, "на list-странице нет initFilterBuilder({...})"
    fields = json.loads(match.group(1))
    assert isinstance(fields, list), fields
    return fields


@pytest.fixture()
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "admin_api_key": ADMIN_TOKEN,
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
def seeded(client: TestClient) -> Iterator[SeededJobs]:
    """4 джобы (queued/running/done/failed) с уникальными акторами.

    Зависит от `client`, чтобы БД была инициализирована приложением.
    """
    actors = _unique_actors()
    ids = asyncio.run(_seed_jobs(actors))
    try:
        yield SeededJobs(actors=actors, ids=ids)
    finally:
        asyncio.run(_delete_jobs(list(ids.values())))


def _list(
    client: TestClient,
    *,
    filter_value: str | None = None,
    sort: str | None = None,
) -> Any:
    params: dict[str, str] = {}
    if filter_value is not None:
        params["filter"] = filter_value
    if sort is not None:
        params["sort"] = sort
    return client.get(VIEW_PATH, headers=AUTH, params=params)


# --- View и список ----------------------------------------------------------


def test_export_job_view_requires_authentication(client: TestClient) -> None:
    response = client.get(VIEW_PATH)
    assert response.status_code == 401
    assert response.json()["code"] == "admin_unauthorized"


def test_export_job_list_shows_seeded_rows(
    client: TestClient, seeded: SeededJobs
) -> None:
    response = _list(client, sort="id__desc")
    assert response.status_code == 200, response.text
    # Свежие строки первыми (сортировка по sortable-полю id).
    for actor in seeded.actors.values():
        assert actor in response.text, actor
    # Журнал зарегистрирован в меню админки с читаемым названием.
    assert "Фоновые выгрузки" in response.text


def test_export_job_list_hides_bulky_columns(
    client: TestClient, seeded: SeededJobs
) -> None:
    """sha256 и JSONB-фильтры не выводятся в список (остаются в деталях)."""
    response = _list(client, sort="id__desc")
    assert response.status_code == 200
    assert "a" * 64 not in response.text
    assert '"schema_version": 2' not in response.text


# --- Status-фильтр в UI -----------------------------------------------------


def test_export_job_list_exposes_status_choices_in_filter_builder(
    client: TestClient, seeded: SeededJobs
) -> None:
    """Filter builder получает EnumField status с 4 значениями и eq/in."""
    response = _list(client)
    assert response.status_code == 200, response.text
    # Разметка самого filter builder присутствует.
    assert 'id="filter-builder-dropdown"' in response.text
    assert 'data-sa-hook="filter-row-template"' in response.text

    fields = {field["name"]: field for field in _filter_builder_fields(response.text)}
    assert "status" in fields, fields
    status_field = fields["status"]
    assert [choice[0] for choice in status_field["choices"]] == list(STATUSES)
    filter_names = {item["name"] for item in status_field["filters"]}
    # eq — одиночный выпадающий список, in — multi-select.
    assert {"eq", "in"} <= filter_names, filter_names


# --- HTTP-фильтры -----------------------------------------------------------


def test_status_eq_filter_returns_only_failed(
    client: TestClient, seeded: SeededJobs
) -> None:
    response = _list(client, filter_value="status__eq=failed")
    assert response.status_code == 200, response.text

    text = response.text
    assert seeded.actors[ExportJobStatus.failed.value] in text
    for status in STATUSES:
        if status == ExportJobStatus.failed.value:
            continue
        assert seeded.actors[status] not in text, status
    # Активная «таблетка» фильтра отрисована в панели списка.
    assert "Status: Equal failed" in text or "status__eq=failed" in text


def test_status_in_filter_returns_done_and_failed(
    client: TestClient, seeded: SeededJobs
) -> None:
    """Multi-select «is one of» — тот же EnumField, значения через запятую."""
    response = _list(client, filter_value="status__in=done,failed")
    assert response.status_code == 200, response.text

    text = response.text
    assert seeded.actors[ExportJobStatus.done.value] in text
    assert seeded.actors[ExportJobStatus.failed.value] in text
    assert seeded.actors[ExportJobStatus.queued.value] not in text
    assert seeded.actors[ExportJobStatus.running.value] not in text


def test_running_filter_finds_hung_jobs(
    client: TestClient, seeded: SeededJobs
) -> None:
    """Сценарий product owner: «зависшие задачи» — статус running."""
    response = _list(client, filter_value="status__eq=running")
    assert response.status_code == 200, response.text
    assert seeded.actors[ExportJobStatus.running.value] in response.text
    assert seeded.actors[ExportJobStatus.done.value] not in response.text


def test_invalid_filter_is_400_not_500(
    client: TestClient, seeded: SeededJobs
) -> None:
    response = _list(client, filter_value="status__bogus=failed")
    assert response.status_code == 400, response.text
