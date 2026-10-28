"""Тесты LLM-адаптера (T-013).

Сеть не используется: `httpx.MockTransport` эмулирует LM Studio и DeepSeek.
Проверяется: маршрутизация уровней, заголовки/ключи, разбор ответа, расход
токенов, ретраи на 429/5xx, ошибки 4xx, таймаут, кривой ответ.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.errors import InvalidLevelError, LLMError, LLMNotConfiguredError, LLMTimeoutError
from app.llm import BaseLLMClient, OpenAICompatibleClient, get_llm_client
from app.llm.openai_client import OpenAICompatibleClient as ClientClass

Handler = Callable[[httpx.Request], httpx.Response]


def _completion(
    text: str = "Привет, шахматы!",
    *,
    model: str = "qwen3-8b",
    prompt_tokens: int = 12,
    completion_tokens: int = 7,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _mock_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "vllm_base_url": "http://127.0.0.1:8034/v1",
        "vllm_model": "qwen3-8b",
        "vllm_api_key": "lm-studio",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-chat",
        "deepseek_api_key": "test-deepseek-key",
        "llm_max_retries": 0,
        "llm_retry_backoff_s": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


async def test_stream_generate_parses_sse_deltas() -> None:
    requests: list[httpx.Request] = []
    body = (
        'data: {"choices":[{"delta":{"content":"Привет"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":", шахматы!"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

    llm = OpenAICompatibleClient(
        base_url="http://127.0.0.1:8034/v1",
        model="qwen3-8b",
        api_key="lm-studio",
        client=_mock_client(handler),
        max_tokens=32,
    )
    chunks = [chunk async for chunk in llm.stream_generate("prompt")]

    assert chunks == ["Привет", ", шахматы!"]
    payload = json.loads(requests[0].content)
    assert payload["stream"] is True
    assert payload["model"] == "qwen3-8b"


async def test_stream_generate_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad model")

    llm = OpenAICompatibleClient(
        base_url="http://127.0.0.1:8034/v1",
        model="qwen3-8b",
        client=_mock_client(handler),
    )
    with pytest.raises(LLMError, match="HTTP 400"):
        _ = [chunk async for chunk in llm.stream_generate("prompt")]


# --- клиент ---------------------------------------------------------------


async def test_completion_parsed_with_usage() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://127.0.0.1:8034/v1",
        model="qwen3-8b",
        api_key="lm-studio",
        provider_name="lmstudio",
        client=client,
    )

    result = await llm.complete("Скажи 'Привет, шахматы!'")

    assert result.text == "Привет, шахматы!"
    assert result.usage.provider == "lmstudio"
    assert result.usage.model == "qwen3-8b"
    assert result.usage.tokens_in == 12
    assert result.usage.tokens_out == 7

    body = json.loads(requests[0].content)
    assert body["model"] == "qwen3-8b"
    assert body["messages"] == [
        {"role": "user", "content": "Скажи 'Привет, шахматы!'"}
    ]
    assert requests[0].headers["authorization"] == "Bearer lm-studio"
    assert str(requests[0].url) == "http://127.0.0.1:8034/v1/chat/completions"

    await client.aclose()


async def test_system_prompt_is_sent_first() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1", model="m", provider_name="test", client=client
    )

    text = await llm.generate("вопрос", system_prompt="ты тренер")

    assert text == "Привет, шахматы!"
    messages = json.loads(captured[0].content)["messages"]
    assert messages[0] == {"role": "system", "content": "ты тренер"}
    assert messages[1]["role"] == "user"

    await client.aclose()


async def test_retry_on_429_then_success() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json=_completion("готово"))

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        max_retries=1,
        retry_backoff_s=0.0,
        client=client,
    )

    assert await llm.generate("x") == "готово"
    assert len(calls) == 2

    await client.aclose()


async def test_retries_exhausted_on_500() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"error": "boom"})

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        max_retries=1,
        retry_backoff_s=0.0,
        client=client,
    )

    with pytest.raises(LLMError, match="HTTP 500"):
        await llm.generate("x")
    assert len(calls) == 2  # первая попытка + один ретрай

    await client.aclose()


async def test_4xx_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "no key"})

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        max_retries=3,
        retry_backoff_s=0.0,
        client=client,
    )

    with pytest.raises(LLMError, match="HTTP 401"):
        await llm.generate("x")
    assert len(calls) == 1

    await client.aclose()


async def test_timeout_raises_llm_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        timeout_s=1.0,
        client=client,
    )

    with pytest.raises(LLMTimeoutError):
        await llm.generate("x")

    await client.aclose()


async def test_malformed_response_raises() -> None:
    client = _mock_client(lambda request: httpx.Response(200, json={"foo": "bar"}))
    llm = OpenAICompatibleClient(
        base_url="http://local/v1", model="m", provider_name="test", client=client
    )

    with pytest.raises(LLMError, match="неожиданный ответ"):
        await llm.generate("x")

    await client.aclose()


def test_client_validates_arguments() -> None:
    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleClient(base_url="http://local/v1", model="")
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleClient(base_url="", model="m")
    with pytest.raises(ValueError, match="max_retries"):
        OpenAICompatibleClient(
            base_url="http://local/v1", model="m", max_retries=-1
        )


# --- фабрика --------------------------------------------------------------


async def test_factory_routes_club_to_local_lmstudio() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    llm = get_llm_client("club", _settings(), client=client)

    assert isinstance(llm, ClientClass)
    assert llm.provider == "lmstudio"
    assert llm.model == "qwen3-8b"
    assert llm.endpoint == "http://127.0.0.1:8034/v1/chat/completions"

    await llm.generate("привет")
    assert str(captured[0].url) == "http://127.0.0.1:8034/v1/chat/completions"
    assert captured[0].headers["authorization"] == "Bearer lm-studio"
    assert json.loads(captured[0].content)["model"] == "qwen3-8b"

    await client.aclose()


async def test_factory_routes_beginner_to_local() -> None:
    client = _mock_client(lambda request: httpx.Response(200, json=_completion()))
    llm = get_llm_client("beginner", _settings(), client=client)

    assert llm.provider == "lmstudio"
    assert isinstance(llm, BaseLLMClient)

    await client.aclose()


async def test_factory_routes_gm_to_deepseek() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion(model="deepseek-chat"))

    client = _mock_client(handler)
    llm = get_llm_client("gm", _settings(), client=client)

    assert llm.provider == "deepseek"
    assert llm.model == "deepseek-chat"
    assert llm.endpoint == "https://api.deepseek.com/chat/completions"

    await llm.generate("привет")
    assert str(captured[0].url) == "https://api.deepseek.com/chat/completions"
    assert captured[0].headers["authorization"] == "Bearer test-deepseek-key"

    await client.aclose()


def test_factory_requires_deepseek_key() -> None:
    with pytest.raises(LLMNotConfiguredError, match="DEEPSEEK_API_KEY"):
        get_llm_client("gm", _settings(deepseek_api_key=""))


def test_factory_rejects_unknown_level() -> None:
    with pytest.raises(InvalidLevelError):
        get_llm_client("master", _settings())
