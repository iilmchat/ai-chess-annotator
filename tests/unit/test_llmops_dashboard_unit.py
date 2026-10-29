"""Unit-тесты чистых хелперов LLMOps-дашборда (T-109) без БД.

SQL-агрегаты проверяются в `tests/integration/test_admin_llmops_dashboard.py`;
здесь — окна, доли, ключи группировки и свойства dataclass-ов.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import Level
from app.llmops_dashboard import (
    PERIODS,
    LatencyStats,
    ModelUsage,
    Period,
    PeriodMetrics,
    TopUserRow,
    _group_key,
    _rate,
)


def test_periods_cover_24h_7d_30d() -> None:
    assert [period.key for period in PERIODS] == ["24h", "7d", "30d"]
    assert [period.delta for period in PERIODS] == [
        timedelta(hours=24),
        timedelta(days=7),
        timedelta(days=30),
    ]


def test_rate_handles_empty_denominator() -> None:
    assert _rate(0, 0) == 0.0
    assert _rate(1, 4) == 0.25
    assert _rate(2, 1) == 2.0


def test_group_key_for_enum_and_null() -> None:
    assert _group_key(Level.club) == "club"
    assert _group_key("model-a") == "model-a"
    assert _group_key(None) == "—"


def test_usage_row_tokens_total() -> None:
    row = ModelUsage(key="model-a", tokens_in=10, tokens_out=5)
    assert row.tokens_total == 15


def test_top_user_subject_label() -> None:
    user = TopUserRow(subject_type="user", subject_key="42")
    assert user.subject == "user:42"


def test_period_metrics_rates_and_empty_latency() -> None:
    metrics = PeriodMetrics(
        period=Period("24h", "24 часа", "последние 24 часа", timedelta(hours=24)),
        since=datetime(2026, 10, 29, tzinfo=UTC),
        requests=4,
        errors=1,
        cache_hits=2,
    )
    assert metrics.error_rate == 0.25
    assert metrics.cache_hit_rate == 0.5
    assert metrics.tokens_total == 0
    assert metrics.latency == LatencyStats()
    assert metrics.latency.p50_ms is None
