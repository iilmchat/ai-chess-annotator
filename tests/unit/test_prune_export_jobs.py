"""Unit-тесты CLI retention `export_jobs` (KI-054) без БД.

Проверяем чистую логику: границу cutoff, валидацию аргументов, JSON-формат
отчёта. SQL/файловая часть — в `tests/integration/test_prune_export_jobs.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import get_settings
from scripts.prune_export_jobs import (
    DEFAULT_BATCH_SIZE,
    PruneStats,
    build_parser,
    resolve_cutoff,
)


def test_resolve_cutoff_uses_utc_now() -> None:
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    cutoff = resolve_cutoff(7, now=now)
    assert cutoff == now - timedelta(days=7)
    assert cutoff.tzinfo is not None


def test_resolve_cutoff_treats_naive_now_as_utc() -> None:
    naive = datetime(2026, 10, 29, 12, 0)
    cutoff = resolve_cutoff(30, now=naive)
    assert cutoff == datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def test_resolve_cutoff_rejects_zero_and_negative() -> None:
    """0 дней удалил бы все экспорты — защищаемся на уровне функции."""
    for days in (0, -1):
        with pytest.raises(ValueError, match=">= 1"):
            resolve_cutoff(days)


def test_parser_defaults_from_settings() -> None:
    args = build_parser().parse_args([])
    assert args.retention_days == get_settings().export_retention_days
    assert args.export_dir == get_settings().export_dir
    assert args.batch_size == DEFAULT_BATCH_SIZE
    assert args.max_batches is None
    assert args.sleep_ms == 0
    assert args.dry_run is False
    assert args.json is False


def test_parser_accepts_overrides() -> None:
    args = build_parser().parse_args(
        [
            "--retention-days",
            "30",
            "--export-dir",
            "/srv/exports",
            "--batch-size",
            "50",
            "--max-batches",
            "3",
            "--sleep-ms",
            "100",
            "--dry-run",
            "--json",
        ]
    )
    assert args.retention_days == 30
    assert args.export_dir == "/srv/exports"
    assert args.batch_size == 50
    assert args.max_batches == 3
    assert args.sleep_ms == 100
    assert args.dry_run is True
    assert args.json is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--retention-days", "0"],
        ["--batch-size", "0"],
        ["--retention-days", "not-a-number"],
    ],
)
def test_parser_rejects_invalid_values(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_prune_stats_json_shape() -> None:
    cutoff = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    stats = PruneStats(
        retention_days=7,
        cutoff=cutoff,
        batch_size=100,
        batches=3,
        deleted_rows=250,
        deleted_files=200,
        missing_files=40,
        skipped_files=10,
        capped=True,
    )
    assert stats.as_dict() == {
        "retention_days": 7,
        "cutoff": "2026-10-22T12:00:00+00:00",
        "batch_size": 100,
        "batches": 3,
        "deleted_rows": 250,
        "deleted_files": 200,
        "missing_files": 40,
        "skipped_files": 10,
        "dry_run": False,
        "capped": True,
    }
