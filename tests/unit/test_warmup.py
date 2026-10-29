"""Тесты прогрева локальной LLM (KI-022 / T-014).

KI-046: прогрев пишется в `llm_calls` с `operation="warmup"` — recorder
вызывается «клиентом» внутри `llm_call_context`, trace генерируется
fallback-ом (HTTP-контекста при старте нет).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.db.models import LlmCall
from app.errors import LLMError
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.llm.calls import (
    current_call_context,
    record_llm_call,
    wait_for_pending_llm_calls,
)
from app.llm.warmup import WARMUP_PROMPT, warmup_local_llm


class _FakeLLM(BaseLLMClient):
    """Клиент-заглушка: отвечает или падает, запоминает промпт."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(model="fake-model")
        self._fail = fail
        self.prompts: list[str] = []
        self.closed = False

    @property
    def provider(self) -> str:
        return "fake"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        if self._fail:
            raise LLMError("LM Studio недоступен")
        return LLMResult(
            text="ok",
            usage=LLMUsage(
                provider="fake", model="fake-model", tokens_in=1, tokens_out=1
            ),
        )

    async def aclose(self) -> None:
        self.closed = True


async def test_warmup_sends_short_prompt() -> None:
    llm = _FakeLLM()

    result = await warmup_local_llm(client=llm)

    assert result is True
    assert llm.prompts == [WARMUP_PROMPT]
    assert llm.closed is False, "внешний клиент не должен закрываться"


async def test_warmup_swallows_provider_errors() -> None:
    llm = _FakeLLM(fail=True)

    result = await warmup_local_llm(client=llm)

    assert result is False
    assert llm.closed is False


async def test_warmup_closes_owned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _FakeLLM()
    closed: list[bool] = []

    async def fake_aclose() -> None:
        closed.append(True)

    llm.aclose = fake_aclose  # type: ignore[method-assign]
    monkeypatch.setattr("app.llm.warmup.get_llm_client", lambda level, cfg: llm)

    result = await warmup_local_llm()

    assert result is True
    assert closed == [True], "свой клиент прогрев обязан закрыть"


# --- KI-046: прогрев попадает в llm_calls ---------------------------------


class _FakeSession:
    """Минимальная async-сессия recorder-а (без БД)."""

    def __init__(self) -> None:
        self.added: list[LlmCall] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def scalar(self, _statement: Any) -> None:
        return None

    def add(self, row: LlmCall) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self) -> _FakeSession:
        return self.session


async def test_warmup_writes_llm_call_with_warmup_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr("app.llm.calls.SessionLocal", _FakeSessionFactory(session))
    operations: list[str | None] = []

    class RecordingLLM(_FakeLLM):
        """Фейк, эмулирующий recorder настоящего клиента."""

        async def complete(
            self, prompt: str, system_prompt: str | None = None
        ) -> LLMResult:
            context = current_call_context()
            operations.append(context.operation if context else None)
            record_llm_call(
                provider="fake", model="fake-model", status="ok", latency_ms=1
            )
            return await super().complete(prompt, system_prompt)

    result = await warmup_local_llm(client=RecordingLLM())
    await wait_for_pending_llm_calls()

    assert result is True
    assert operations == ["warmup"]
    assert len(session.added) == 1
    row = session.added[0]
    assert row.operation == "warmup"
    assert row.provider == "fake"
    assert row.model == "fake-model"
    assert row.status == "ok"
    assert row.actor_id is None
    assert row.game_id is None
    assert row.commentary_id is None
    assert row.cache_hit is False
    # Вне HTTP trace id сгенерирован recorder-ом (UUID v4).
    assert row.trace_id is not None
    assert row.trace_id.version == 4

