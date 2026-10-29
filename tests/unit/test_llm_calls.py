"""Recorder `llm_calls` (T-030): контекст вызова и фоновая запись.

БД не нужна: `SessionLocal` подменяется фейковой фабрикой сессий, а
`_persist_call` в тестах «no-op» подменяется явно. Проверяем scoping
contextvar-ов, метрики/статусы, prompt_version из `prompts.version`,
trace/span-идентификаторы и fire-and-forget (запись не блокирует вызов).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.db.models import Level, LlmCall
from app.llm import calls
from app.llm.calls import (
    current_call_context,
    llm_call_context,
    record_llm_call,
    stream_in_call_context,
    wait_for_pending_llm_calls,
)
from app.tracing import TraceContext, trace_context


class FakeSession:
    """Минимальная async-сессия: scalar/add/commit."""

    def __init__(self, *, prompt_version: int | None = 1) -> None:
        self.added: list[LlmCall] = []
        self.prompt_version = prompt_version
        self.scalar_calls = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def scalar(self, _statement: Any) -> int | None:
        self.scalar_calls += 1
        return self.prompt_version

    def add(self, row: LlmCall) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        return None


class FakeSessionFactory:
    """Замена `SessionLocal`: каждый `with` отдаёт одну и ту же сессию."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    session = FakeSession()
    monkeypatch.setattr(calls, "SessionLocal", FakeSessionFactory(session))
    return session


def _trace_context() -> TraceContext:
    return TraceContext(
        trace_id=uuid4(),
        span_id="0123456789abcdef",
        request_id="req-1",
    )


# --- scoping contextvar-ов -------------------------------------------------


def test_call_context_is_scoped_and_nested() -> None:
    assert current_call_context() is None
    with llm_call_context(
        operation="chat", actor_id=1, game_id=2
    ) as outer:
        assert current_call_context() is outer
        assert outer.operation == "chat"
        assert outer.actor_id == 1
        assert outer.game_id == 2
        with llm_call_context(operation="commentary") as inner:
            assert current_call_context() is inner
            assert inner.operation == "commentary"
        assert current_call_context() is outer
    assert current_call_context() is None


async def test_stream_in_call_context_keeps_context_while_iterating() -> None:
    seen: list[str] = []

    async def source() -> AsyncIterator[str]:
        for chunk in ("a", "b"):
            context = current_call_context()
            assert context is not None
            seen.append(context.operation)
            yield chunk

    chunks = [
        chunk
        async for chunk in stream_in_call_context(
            source(), operation="chat", actor_id=7, game_id=9
        )
    ]

    assert chunks == ["a", "b"]
    assert seen == ["chat", "chat"]
    assert current_call_context() is None


# --- запись -----------------------------------------------------------------


async def test_record_llm_call_writes_full_row(fake_session: FakeSession) -> None:
    context = _trace_context()
    with trace_context(context):
        with llm_call_context(
            operation="commentary", actor_id=5, game_id=11
        ):
            record_llm_call(
                provider="lmstudio",
                model="qwen/qwen3-4b-2507",
                status="ok",
                latency_ms=42,
                tokens_in=10,
                tokens_out=3,
                cost_usd=0.000123,
                level="club",
                cache_hit=False,
            )
            # fire-and-forget: до ближайшего await строки ещё нет.
            assert fake_session.added == []
            await wait_for_pending_llm_calls()

    assert len(fake_session.added) == 1
    row = fake_session.added[0]
    assert isinstance(row.trace_id, UUID)
    assert row.trace_id == context.trace_id
    assert len(row.span_id) == 16
    assert row.request_id == "req-1"
    assert row.operation == "commentary"
    assert row.actor_id == 5
    assert row.game_id == 11
    assert row.commentary_id is None
    assert row.provider == "lmstudio"
    assert row.model == "qwen/qwen3-4b-2507"
    assert row.level == Level.club
    assert row.prompt_slug == "club"
    assert row.prompt_version == 1
    assert row.tokens_in == 10
    assert row.tokens_out == 3
    assert row.cost_usd == Decimal("0.000123")
    assert row.latency_ms == 42
    assert row.cache_hit is False
    assert row.status == "ok"
    assert row.error_code is None
    assert fake_session.scalar_calls == 1


async def test_record_llm_call_generates_span_per_call(
    fake_session: FakeSession,
) -> None:
    """В одном запросе бывает несколько вызовов (retry) — span_id разный."""
    with trace_context(_trace_context()):
        with llm_call_context(operation="commentary"):
            record_llm_call(
                provider="lmstudio",
                model="m",
                status="ok",
                latency_ms=1,
                level="club",
            )
            record_llm_call(
                provider="lmstudio",
                model="m",
                status="error",
                latency_ms=1,
                level="club",
                error_code="llm_error",
            )
            await wait_for_pending_llm_calls()

    assert len(fake_session.added) == 2
    first, second = fake_session.added
    assert first.trace_id == second.trace_id
    assert first.span_id != second.span_id
    assert {len(row.span_id) for row in fake_session.added} == {16}
    assert second.status == "error"
    assert second.error_code == "llm_error"


async def test_record_llm_call_uses_context_prompt_version(
    fake_session: FakeSession,
) -> None:
    with trace_context(_trace_context()):
        with llm_call_context(operation="chat", prompt_version=4):
            record_llm_call(
                provider="deepseek",
                model="deepseek-chat",
                status="timeout",
                latency_ms=500,
                error_code="llm_timeout",
                level="gm",
            )
            await wait_for_pending_llm_calls()

    row = fake_session.added[0]
    assert row.prompt_version == 4
    assert row.status == "timeout"
    assert fake_session.scalar_calls == 0


async def test_record_llm_call_without_prompt_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession(prompt_version=None)
    monkeypatch.setattr(calls, "SessionLocal", FakeSessionFactory(session))
    with trace_context(_trace_context()):
        with llm_call_context(operation="chat"):
            record_llm_call(
                provider="fake", model="m", status="ok", latency_ms=1, level="club"
            )
            await wait_for_pending_llm_calls()

    assert session.added[0].prompt_version is None


async def test_record_llm_call_generates_trace_outside_http(
    fake_session: FakeSession,
) -> None:
    """Вне HTTP trace нет — recorder создаёт свой (UUID v4)."""
    with llm_call_context(operation="warmup"):
        record_llm_call(
            provider="lmstudio", model="m", status="ok", latency_ms=1
        )
        await wait_for_pending_llm_calls()

    row = fake_session.added[0]
    assert isinstance(row.trace_id, UUID)
    assert row.trace_id.version == 4
    assert row.operation == "warmup"
    assert row.level is None
    assert row.prompt_slug is None


async def test_record_llm_call_is_noop_outside_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[object] = []

    async def fake_persist(record: object) -> None:
        persisted.append(record)

    # Без trace-контекста событие не должно даже ставиться в фон.
    monkeypatch.setattr("app.llm.calls._persist_call", fake_persist)
    record_llm_call(provider="fake", model="m", status="ok", latency_ms=1)
    await wait_for_pending_llm_calls()

    assert persisted == []


async def test_record_llm_call_clamps_negative_metrics(
    fake_session: FakeSession,
) -> None:
    with trace_context(_trace_context()):
        with llm_call_context(operation="chat"):
            record_llm_call(
                provider="fake",
                model="m",
                status="ok",
                latency_ms=-5,
                tokens_in=-1,
                tokens_out=-1,
                cost_usd=-0.5,
            )
            await wait_for_pending_llm_calls()

    row = fake_session.added[0]
    assert row.tokens_in == 0
    assert row.tokens_out == 0
    assert row.cost_usd == Decimal("0")
    assert row.latency_ms == 0


async def test_persist_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenSession(FakeSession):
        async def commit(self) -> None:
            raise RuntimeError("db down")

    session = BrokenSession()
    monkeypatch.setattr(calls, "SessionLocal", FakeSessionFactory(session))

    with trace_context(_trace_context()):
        with llm_call_context(operation="chat"):
            record_llm_call(
                provider="fake", model="m", status="ok", latency_ms=1
            )
            await wait_for_pending_llm_calls()

    assert len(session.added) == 1  # строка поставлена, commit упал — не упало
