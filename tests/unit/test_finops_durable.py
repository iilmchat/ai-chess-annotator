"""Unit-тесты durable-агрегатов FinOps (T-031b).

Проверяем чистую логику без БД: разбор субъекта, UTC-дату, постановку
фонового upsert-а, fail-open поведение и разбор `--month` у
`scripts/aggregate_usage.py`. SQL-часть (`ON CONFLICT`) и скрипт на живой
БД — в `tests/integration/test_usage_daily.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import pytest

import app.finops as finops
from scripts.aggregate_usage import (
    UsageTotals,
    build_parser,
    month_range,
    parse_month,
)


def test_parse_subject_user_and_ip() -> None:
    assert finops.parse_subject("user:42") == ("user", "42")
    assert finops.parse_subject("ip:1.2.3.4") == ("ip", "1.2.3.4")


@pytest.mark.parametrize(
    "subject",
    ["", "user", "user:", "bot:1", ":42", "user:42:extra"],
)
def test_parse_subject_rejects_unknown(subject: str) -> None:
    if subject == "user:42:extra":
        # partition(":") оставляет остаток в ключе — это допустимый user-id.
        assert finops.parse_subject(subject) == ("user", "42:extra")
        return
    with pytest.raises(ValueError):
        finops.parse_subject(subject)


def test_utc_date_uses_utc() -> None:
    moment = datetime(2026, 10, 29, 23, 30, tzinfo=UTC)
    assert finops.utc_date(moment) == date(2026, 10, 29)


def test_record_token_usage_without_subject_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(finops, "_upsert_usage_daily", fake_upsert)
    asyncio.run(finops.record_token_usage(10, 5, cost_usd=0.1))
    assert calls == []


def test_record_token_usage_schedules_durable_upsert_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без трекера (или при сбое Redis) durable-запись всё равно пишется."""
    calls: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(finops, "_upsert_usage_daily", fake_upsert)

    class BrokenTracker:
        async def add(self, subject: str, tokens: int) -> None:
            raise RuntimeError("redis down")

    subject_token = finops._current_subject.set("user:7")
    tracker_token = finops._current_tracker.set(BrokenTracker())  # type: ignore[arg-type]
    try:
        asyncio.run(finops.record_token_usage(11, 7, cost_usd=0.25))
        asyncio.run(finops.wait_for_pending_usage_upserts())
    finally:
        finops._current_subject.reset(subject_token)
        finops._current_tracker.reset(tracker_token)

    assert len(calls) == 1
    assert calls[0]["subject"] == "user:7"
    assert calls[0]["tokens_in"] == 11
    assert calls[0]["tokens_out"] == 7
    assert calls[0]["cost_usd"] == 0.25


def test_record_token_usage_increments_redis_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    added: list[tuple[str, int]] = []

    class FakeTracker:
        async def add(self, subject: str, tokens: int) -> None:
            added.append((subject, tokens))

    async def fake_upsert(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(finops, "_upsert_usage_daily", fake_upsert)
    subject_token = finops._current_subject.set("ip:127.0.0.1")
    tracker_token = finops._current_tracker.set(FakeTracker())  # type: ignore[arg-type]
    try:
        asyncio.run(finops.record_token_usage(3, 4))
        asyncio.run(finops.wait_for_pending_usage_upserts())
    finally:
        finops._current_subject.reset(subject_token)
        finops._current_tracker.reset(tracker_token)

    assert added == [("ip:127.0.0.1", 7)]


def test_upsert_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой БД в фоновой задаче не пробрасывается и не ломает запрос."""

    class BrokenSessionLocal:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    monkeypatch.setattr(finops, "SessionLocal", BrokenSessionLocal())
    asyncio.run(
        finops._upsert_usage_daily(
            subject="user:1", tokens_in=1, tokens_out=2, cost_usd=0.0
        )
    )


def test_usage_daily_tokens_invalid_subject_returns_none() -> None:
    assert asyncio.run(finops.usage_daily_tokens("nonsense")) is None


def test_aggregate_month_range_and_parse() -> None:
    assert month_range(2026, 10) == (date(2026, 10, 1), date(2026, 11, 1))
    assert month_range(2026, 12) == (date(2026, 12, 1), date(2027, 1, 1))
    assert parse_month("2026-02") == (date(2026, 2, 1), date(2026, 3, 1))


@pytest.mark.parametrize("value", ["2026-13", "2026-1", "bad", "2026-00"])
def test_aggregate_parse_month_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_month(value)


def test_aggregate_parser_defaults_and_flags() -> None:
    parser = build_parser()
    default = parser.parse_args([])
    assert default.retention_days == 90
    assert default.no_cleanup is False
    assert default.json is False

    parsed = parser.parse_args(
        ["--month", "2026-10", "--json", "--retention-days", "0", "--no-cleanup"]
    )
    assert parsed.month == (date(2026, 10, 1), date(2026, 11, 1))
    assert parsed.json is True
    assert parsed.no_cleanup is True


def test_usage_totals_dict() -> None:
    totals = UsageTotals(tokens_in=10, tokens_out=5)
    assert totals.tokens_total == 15
    assert totals.as_dict()["tokens_total"] == 15
