"""Тесты прогрева локальной LLM (KI-022 / T-014)."""

from __future__ import annotations

import pytest

from app.errors import LLMError
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
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
