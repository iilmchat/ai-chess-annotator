"""Интеграционные тесты retention `export_jobs` и `EXPORT_DIR` (KI-054).

Проверяем реальную БД и файловую систему:

* старые строки и их файлы удаляются, свежие остаются;
* удаление идёт батчами, `--max-batches` ограничивает запуск;
* `--dry-run` считает, но ничего не удаляет;
* файл вне `EXPORT_DIR` не удаляется (защита от подмены `file_path`);
* отсутствующий файл не роняет очистку;
* CLI `main([...])` печатает JSON-отчёт и возвращает exit code 0.

Тестовые строки помечены префиксом `actor = "prune-export-…"` и удаляются
после теста. Требуется PostgreSQL; без него модуль скипается.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import ExportJob, ExportJobStatus
from scripts.prune_export_jobs import main as prune_main
from scripts.prune_export_jobs import prune_batches

#: Дни, старше которых джобы считаются «старыми» во всех тестах.
RETENTION_DAYS = 7

#: Префикс actor тестовых строк — для точечной уборки.
ACTOR_PREFIX = "prune-export-"


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


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _write(path: Path, content: str) -> None:
    """Записать файл в потоке (ASYNC240: не блокируем event loop)."""
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


async def _seed(
    export_dir: Path,
    *,
    old: int,
    recent: int,
    old_without_file: int = 0,
    outside_file: Path | None = None,
) -> tuple[list[int], list[int], list[Path]]:
    """Создать старые/свежие джобы и файлы; вернуть id и пути файлов."""
    now = datetime.now(UTC)
    await asyncio.to_thread(export_dir.mkdir, parents=True, exist_ok=True)
    old_ids: list[int] = []
    recent_ids: list[int] = []
    files: list[Path] = []
    factory = _factory()
    async with factory() as session:
        for index in range(old):
            job = ExportJob(
                status=ExportJobStatus.done.value,
                actor=f"{ACTOR_PREFIX}old-{index}-{uuid4().hex[:8]}",
                created_at=now - timedelta(days=400 + index),
            )
            session.add(job)
            await session.flush()
            path = export_dir / f"edits-{job.id}.jsonl"
            await _write(path, '{"line": 1}\n')
            job.file_path = str(path)
            old_ids.append(job.id)
            files.append(path)
        for index in range(old_without_file):
            job = ExportJob(
                status=ExportJobStatus.failed.value,
                actor=f"{ACTOR_PREFIX}missing-{index}-{uuid4().hex[:8]}",
                created_at=now - timedelta(days=400 + index),
                error="test missing file",
            )
            session.add(job)
            await session.flush()
            # file_path указывает на канонический путь, но файла нет.
            job.file_path = str(export_dir / f"edits-{job.id}.jsonl")
            old_ids.append(job.id)
        if outside_file is not None:
            await _write(outside_file, '{"outside": true}\n')
            job = ExportJob(
                status=ExportJobStatus.done.value,
                actor=f"{ACTOR_PREFIX}outside-{uuid4().hex[:8]}",
                created_at=now - timedelta(days=400),
                file_path=str(outside_file),
            )
            session.add(job)
            await session.flush()
            old_ids.append(job.id)
        for index in range(recent):
            job = ExportJob(
                status=ExportJobStatus.done.value,
                actor=f"{ACTOR_PREFIX}new-{index}-{uuid4().hex[:8]}",
                created_at=now - timedelta(days=index),
            )
            session.add(job)
            await session.flush()
            path = export_dir / f"edits-{job.id}.jsonl"
            await _write(path, '{"line": 2}\n')
            job.file_path = str(path)
            recent_ids.append(job.id)
            files.append(path)
        await session.commit()
    return old_ids, recent_ids, files


async def _cleanup() -> None:
    """Удалить все строки, созданные этими тестами."""
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(ExportJob).where(
                    ExportJob.actor.like(f"{ACTOR_PREFIX}%")
                )
            )
    finally:
        await engine.dispose()


async def _existing_ids(ids: list[int]) -> set[int]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = await session.scalars(
                select(ExportJob.id).where(ExportJob.id.in_(ids))
            )
            return set(rows)
    finally:
        await engine.dispose()


async def _run(
    export_dir: Path,
    *,
    batch_size: int = 100,
    max_batches: int | None = None,
    dry_run: bool = False,
) -> Any:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await prune_batches(
                session,
                retention_days=RETENTION_DAYS,
                export_dir=export_dir,
                batch_size=batch_size,
                max_batches=max_batches,
                dry_run=dry_run,
            )
    finally:
        await engine.dispose()


def test_prune_deletes_old_rows_and_files(
    tmp_path: Path,
) -> None:
    export_dir = tmp_path / "exports"

    async def scenario() -> None:
        await _cleanup()
        old_ids, recent_ids, _ = await _seed(
            export_dir, old=3, recent=2
        )
        stats = await _run(export_dir, batch_size=2)
        assert stats.deleted_rows == 3
        assert stats.deleted_files == 3
        assert stats.batches >= 2
        assert stats.capped is False
        assert await _existing_ids(old_ids) == set()
        assert await _existing_ids(recent_ids) == set(recent_ids)
        for job_id in old_ids:
            assert not (export_dir / f"edits-{job_id}.jsonl").exists()
        for job_id in recent_ids:
            assert (export_dir / f"edits-{job_id}.jsonl").is_file()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_prune_max_batches_caps_run(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"

    async def scenario() -> None:
        await _cleanup()
        old_ids, _, _ = await _seed(export_dir, old=3, recent=0)
        stats = await _run(export_dir, batch_size=1, max_batches=1)
        assert stats.deleted_rows == 1
        assert stats.batches == 1
        assert stats.capped is True
        # Остальные старые джобы добьёт следующий запуск.
        assert len(await _existing_ids(old_ids)) == 2

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_prune_dry_run_counts_without_deleting(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"

    async def scenario() -> None:
        await _cleanup()
        old_ids, recent_ids, _ = await _seed(
            export_dir, old=2, recent=1
        )
        stats = await _run(export_dir, dry_run=True)
        assert stats.dry_run is True
        assert stats.deleted_rows == 2
        assert stats.deleted_files == 2
        assert stats.batches == 0
        assert await _existing_ids(old_ids) == set(old_ids)
        assert await _existing_ids(recent_ids) == set(recent_ids)
        for job_id in old_ids:
            assert (export_dir / f"edits-{job_id}.jsonl").is_file()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_prune_skips_file_outside_export_dir(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    outside = tmp_path / "outside.jsonl"

    async def scenario() -> None:
        await _cleanup()
        old_ids, _, _ = await _seed(
            export_dir, old=0, recent=0, outside_file=outside
        )
        stats = await _run(export_dir)
        assert stats.deleted_rows == 1
        assert stats.skipped_files >= 1
        # Строка удалена, но файл вне EXPORT_DIR не тронут.
        assert await _existing_ids(old_ids) == set()
        assert outside.is_file()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_prune_tolerates_missing_file(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"

    async def scenario() -> None:
        await _cleanup()
        old_ids, _, _ = await _seed(
            export_dir, old=0, recent=0, old_without_file=1
        )
        stats = await _run(export_dir)
        assert stats.deleted_rows == 1
        assert stats.deleted_files == 0
        assert stats.missing_files >= 1
        assert await _existing_ids(old_ids) == set()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_cli_main_prints_json_and_deletes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export_dir = tmp_path / "exports"

    async def seed() -> list[int]:
        await _cleanup()
        old_ids, _, _ = await _seed(export_dir, old=2, recent=1)
        return old_ids

    async def remaining(ids: list[int]) -> set[int]:
        return await _existing_ids(ids)

    try:
        old_ids = asyncio.run(seed())
        code = prune_main(
            [
                "--retention-days",
                str(RETENTION_DAYS),
                "--export-dir",
                str(export_dir),
                "--batch-size",
                "100",
                "--json",
            ]
        )
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["retention_days"] == RETENTION_DAYS
        assert report["deleted_rows"] == 2
        assert report["deleted_files"] == 2
        assert report["dry_run"] is False
        assert asyncio.run(remaining(old_ids)) == set()
        for job_id in old_ids:
            assert not (export_dir / f"edits-{job_id}.jsonl").exists()
    finally:
        asyncio.run(_cleanup())
