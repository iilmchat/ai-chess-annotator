"""Лимит ответа судьи LLM-as-judge (итерация 61).

Судья возвращает JSON с тремя оценками и коротким комментарием. При общем
лимите `LLM_MAX_TOKENS=1024` ответ обрезался, и `_extract_json` падал с
«в ответе судьи нет JSON-объекта» — в baseline v3 так потерялись 2 из 5
партий. Тесты проверяют, что:

* у судьи отдельная настройка `LLM_JUDGE_MAX_TOKENS` (по умолчанию 2048),
  а общий лимит комментариев остаётся 1024;
* `_judge_client` действительно отправляет `max_tokens` судьи в payload;
* `_judge` собирает клиента через `_judge_client` и штатно разбирает JSON.

Сеть не используется: `httpx.MockTransport` и фейковый клиент.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from scripts import evaluate_llm
from scripts.evaluate_llm import (
    JUDGE_SYSTEM,
    CommentaryRow,
    GameRow,
    _judge,
    _judge_client,
)

Handler = Callable[[httpx.Request], httpx.Response]

GAME = GameRow(
    game_id=-1,
    pgn="1. e4 e5",
    white="Stein, Leonid",
    black="Geller, Efim",
    event="Moscow zt",
)

JUDGE_JSON = (
    '{"factual_accuracy": 7, "usefulness": 8, "style": 9, '
    '"comment": "Хороший разбор."}'
)


def _completion(text: str = JUDGE_JSON) -> dict[str, Any]:
    return {
        "id": "chatcmpl-judge",
        "object": "chat.completion",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def _mock_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-chat",
        "deepseek_api_key": "test-deepseek-key",
        "llm_max_retries": 0,
        "llm_retry_backoff_s": 0.0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- настройки ---------------------------------------------------------------


def test_judge_max_tokens_default_is_2048() -> None:
    cfg = Settings(_env_file=None)

    assert cfg.llm_judge_max_tokens == 2048
    # Общий лимит комментариев не меняется — судья получает свой.
    assert cfg.llm_max_tokens == 1024


def test_judge_max_tokens_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_JUDGE_MAX_TOKENS", "4096")

    assert Settings(_env_file=None).llm_judge_max_tokens == 4096


# --- клиент судьи ------------------------------------------------------------


async def test_judge_client_sends_judge_max_tokens() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    judge = _judge_client(_settings(), client=client)

    result = await judge.complete("оцени комментарий", system_prompt=JUDGE_SYSTEM)

    payload = json.loads(captured[0].content)
    assert payload["max_tokens"] == 2048
    assert payload["model"] == "deepseek-chat"
    assert result.usage.provider == "deepseek"
    assert str(captured[0].url) == "https://api.deepseek.com/chat/completions"

    await client.aclose()


async def test_judge_client_honours_custom_limit() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    judge = _judge_client(
        _settings(llm_judge_max_tokens=4096), client=client
    )

    await judge.complete("оцени")

    assert json.loads(captured[0].content)["max_tokens"] == 4096

    await client.aclose()


# --- _judge ------------------------------------------------------------------


class _FakeJudgeClient(BaseLLMClient):
    """Фейковый клиент судьи: запоминает промпты и отвечает JSON."""

    def __init__(self, text: str = JUDGE_JSON) -> None:
        super().__init__(model="deepseek-chat")
        self.text = text
        self.prompts: list[str] = []
        self.closed = False

    @property
    def provider(self) -> str:
        return "deepseek"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            text=self.text,
            usage=LLMUsage(
                provider="deepseek",
                model="deepseek-chat",
                tokens_in=100,
                tokens_out=50,
            ),
        )

    async def aclose(self) -> None:
        self.closed = True


async def test_judge_uses_judge_max_tokens_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_judge` собирает клиента с `llm_max_tokens=LLM_JUDGE_MAX_TOKENS`."""
    captured: list[Settings] = []
    fake = _FakeJudgeClient()

    def fake_factory(
        level: str, settings: Settings, *, client: Any = None
    ) -> _FakeJudgeClient:
        captured.append(settings)
        return fake

    monkeypatch.setattr(evaluate_llm, "get_llm_client", fake_factory)
    row = CommentaryRow(game=GAME, level="club", text="Белые захватили центр.")
    row.fact_sheet = "Белые: Stein\nЧёрные: Geller"

    await _judge([row], _settings())

    assert len(captured) == 1
    assert captured[0].llm_max_tokens == 2048
    assert captured[0].llm_judge_max_tokens == 2048
    assert row.scores == {
        "factual_accuracy": 7.0,
        "usefulness": 8.0,
        "style": 9.0,
    }
    assert row.judge_comment == "Хороший разбор."
    assert row.judge_tokens == 150
    assert fake.closed is True


async def test_judge_records_truncated_json_as_row_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если судья всё же вернул обрезанный ответ — ошибка только у строки."""
    fake = _FakeJudgeClient(text='{"factual_accuracy": 7, "useful')

    monkeypatch.setattr(
        evaluate_llm,
        "get_llm_client",
        lambda level, settings, *, client=None: fake,
    )
    row = CommentaryRow(game=GAME, level="club", text="Белые захватили центр.")

    await _judge([row], _settings())

    assert row.scores == {}
    assert row.error is not None
    assert "JSON-объекта" in row.error
    assert fake.closed is True
