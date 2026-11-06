"""Тесты LLM-адаптера (T-013, T-030).

Сеть не используется: `httpx.MockTransport` эмулирует LM Studio и DeepSeek.
Проверяется: маршрутизация уровней, заголовки/ключи, разбор ответа, расход
токенов, ретраи на 429/5xx, ошибки 4xx, таймаут, кривой ответ.

T-030: клиент обязан сообщать метрики вызова в recorder `llm_calls` —
проверяем статусы (ok/error/timeout/invalid), стрим (включая отмену) и
отсутствие текстов промпта/ответа в записи.
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
from app.llm.router import BackendRouter

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
        price_in_usd_per_1m=0.27,
        price_out_usd_per_1m=1.10,
    )

    result = await llm.complete("Скажи 'Привет, шахматы!'")

    assert result.text == "Привет, шахматы!"
    assert result.usage.provider == "lmstudio"
    assert result.usage.model == "qwen3-8b"
    assert result.usage.tokens_in == 12
    assert result.usage.tokens_out == 7
    # Стоимость округляется до 6 знаков в `compute_cost_usd`.
    assert result.usage.cost_usd == pytest.approx(0.000011)

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


# --- LLM_TEMPERATURE (итерация 48) ----------------------------------------


def test_temperature_env_var_maps_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`LLM_TEMPERATURE` из окружения попадает в `Settings.llm_temperature`."""
    monkeypatch.setenv("LLM_TEMPERATURE", "0.55")

    assert Settings(_env_file=None).llm_temperature == pytest.approx(0.55)


@pytest.mark.parametrize(
    ("level", "expected_model"),
    [("club", "qwen3-8b"), ("gm", "deepseek-chat")],
)
async def test_factory_sends_settings_temperature_in_payload(
    level: str, expected_model: str
) -> None:
    """Фабрика пробрасывает `llm_temperature` в JSON-запрос провайдера."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion(model=expected_model))

    client = _mock_client(handler)
    llm = get_llm_client(
        level, _settings(llm_temperature=0.65), client=client
    )

    await llm.generate("привет")

    payload = json.loads(captured[0].content)
    assert payload["model"] == expected_model
    assert payload["temperature"] == pytest.approx(0.65)

    await client.aclose()


async def test_stream_payload_carries_temperature() -> None:
    """Стрим использует тот же `_build_payload` и тоже передаёт температуру."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            content=_stream_body("e4"),
            headers={"content-type": "text/event-stream"},
        )

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        client=client,
        temperature=0.9,
    )

    chunks = [chunk async for chunk in llm.stream_generate("prompt")]

    assert chunks == ["e4"]
    assert json.loads(captured[0].content)["temperature"] == pytest.approx(0.9)

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


async def test_factory_routes_club_to_local_first_router() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    llm = get_llm_client("club", _settings(), client=client)

    assert isinstance(llm, BackendRouter)
    assert llm.provider == "lmstudio"
    assert llm.model == "qwen3-8b"
    assert len(llm.backends) == 2
    assert llm.primary.provider == "lmstudio"
    assert llm.fallback is not None
    assert llm.fallback.provider == "deepseek"

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


async def test_factory_club_falls_back_to_deepseek_when_local_down() -> None:
    """T-123: локальный GPU недоступен — запрос уходит в облако."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "127.0.0.1":
            raise httpx.ConnectError("local gpu down", request=request)
        return httpx.Response(200, json=_completion(model="deepseek-chat"))

    client = _mock_client(handler)
    llm = get_llm_client("club", _settings(), client=client)

    result = await llm.complete("привет")

    assert result.usage.provider == "deepseek"
    assert result.usage.model == "deepseek-chat"
    assert [str(request.url) for request in requests] == [
        "http://127.0.0.1:8034/v1/chat/completions",
        "https://api.deepseek.com/chat/completions",
    ]

    await client.aclose()


async def test_factory_club_without_deepseek_key_is_local_only() -> None:
    """Без ключа облака роутер остаётся локальным (fallback выключен)."""
    client = _mock_client(lambda request: httpx.Response(200, json=_completion()))
    llm = get_llm_client("club", _settings(deepseek_api_key=""), client=client)

    assert isinstance(llm, BackendRouter)
    assert len(llm.backends) == 1
    assert llm.fallback is None

    await client.aclose()


def test_factory_requires_deepseek_key() -> None:
    with pytest.raises(LLMNotConfiguredError, match="DEEPSEEK_API_KEY"):
        get_llm_client("gm", _settings(deepseek_api_key=""))


def test_factory_rejects_unknown_level() -> None:
    with pytest.raises(InvalidLevelError):
        get_llm_client("master", _settings())


# --- T-030: запись вызова в llm_calls -------------------------------------


def _captured_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Перехватить вызовы recorder-а (без фоновой задачи и без БД)."""
    captured: list[dict[str, Any]] = []

    def fake_record(**kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr("app.llm.openai_client.record_llm_call", fake_record)
    return captured


def _stream_body(*deltas: str) -> bytes:
    lines = [
        "data: "
        + json.dumps({"choices": [{"delta": {"content": delta}}]})
        + "\n\n"
        for delta in deltas
    ]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


async def test_complete_reports_metrics_to_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _captured_calls(monkeypatch)
    client = _mock_client(lambda request: httpx.Response(200, json=_completion()))
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        client=client,
        price_in_usd_per_1m=0.27,
        price_out_usd_per_1m=1.10,
    )
    llm.set_level("club")

    await llm.generate("секретный промпт")

    assert len(recorded) == 1
    call = recorded[0]
    assert call["provider"] == "test"
    assert call["model"] == "m"
    assert call["status"] == "ok"
    assert call["tokens_in"] == 12
    assert call["tokens_out"] == 7
    assert call["cost_usd"] == pytest.approx(0.000011)
    assert call["error_code"] is None
    assert call["level"] == "club"
    assert call["cache_hit"] is False
    assert call["latency_ms"] >= 0
    # Privacy: ни текста промпта, ни ответа в записи быть не должно.
    assert not {"prompt", "text", "system_prompt", "response"} & set(call)

    await client.aclose()


async def test_complete_reports_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _captured_calls(monkeypatch)
    client = _mock_client(lambda request: httpx.Response(400, text="bad"))
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        client=client,
        max_retries=0,
    )

    with pytest.raises(LLMError):
        await llm.generate("x")

    assert recorded[0]["status"] == "error"
    assert recorded[0]["error_code"] == "llm_error"
    assert recorded[0]["tokens_in"] == 0
    assert recorded[0]["tokens_out"] == 0

    await client.aclose()


async def test_complete_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _captured_calls(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

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

    assert recorded[0]["status"] == "timeout"
    assert recorded[0]["error_code"] == "llm_timeout"

    await client.aclose()


async def test_complete_reports_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _captured_calls(monkeypatch)
    client = _mock_client(lambda request: httpx.Response(200, json={"foo": "bar"}))
    llm = OpenAICompatibleClient(
        base_url="http://local/v1", model="m", provider_name="test", client=client
    )

    with pytest.raises(LLMError, match="неожиданный ответ"):
        await llm.generate("x")

    assert recorded[0]["status"] == "invalid"
    assert recorded[0]["error_code"] == "invalid_response"

    await client.aclose()


async def test_stream_reports_metrics_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _captured_calls(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_stream_body("Привет", ", шахматы!"),
            headers={"content-type": "text/event-stream"},
        )

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        client=client,
        price_in_usd_per_1m=1.0,
        price_out_usd_per_1m=2.0,
    )
    llm.set_level("club")

    chunks = [chunk async for chunk in llm.stream_generate("prompt")]

    assert chunks == ["Привет", ", шахматы!"]
    call = recorded[0]
    assert call["status"] == "ok"
    assert call["tokens_in"] > 0  # estimate_tokens(prompt)
    assert call["tokens_out"] > 0  # оценка по символам
    assert call["level"] == "club"
    assert call["cache_hit"] is False

    await client.aclose()


async def test_stream_reports_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _captured_calls(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad model")

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1",
        model="m",
        provider_name="test",
        client=client,
        max_retries=0,
    )

    with pytest.raises(LLMError):
        _ = [chunk async for chunk in llm.stream_generate("prompt")]

    call = recorded[0]
    assert call["status"] == "error"
    assert call["error_code"] == "llm_error"
    assert call["tokens_out"] == 0

    await client.aclose()


async def test_stream_reports_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Закрытие стрима потребителем — тоже вызов, статус error."""
    recorded = _captured_calls(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_stream_body("e4"),
            headers={"content-type": "text/event-stream"},
        )

    client = _mock_client(handler)
    llm = OpenAICompatibleClient(
        base_url="http://local/v1", model="m", provider_name="test", client=client
    )

    stream = llm.stream_generate("prompt")
    assert await stream.__anext__() == "e4"
    await stream.aclose()

    call = recorded[0]
    assert call["status"] == "error"
    assert call["error_code"] == "client_disconnect"

    await client.aclose()



# --- T-123 / итерация 59: настройки GPU-туннеля ----------------------------


def test_local_llm_base_url_defaults_to_lmstudio() -> None:
    """Без env-настроек роутер смотрит на прямой LM Studio."""
    cfg = Settings(_env_file=None)

    assert cfg.local_llm_base_url == "http://127.0.0.1:8034/v1"
    assert cfg.llm_local_timeout_s == pytest.approx(2.0)
    assert cfg.llm_router_cooldown_s == pytest.approx(60.0)


def test_tunnel_settings_are_read_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`LLM_LOCAL_*` / cooldown читаются из окружения (деплой на VPS)."""
    monkeypatch.setenv("LLM_LOCAL_BACKEND_URL", "http://172.17.0.1:8034/v1/")
    monkeypatch.setenv("LLM_LOCAL_TIMEOUT_S", "3.5")
    monkeypatch.setenv("LLM_ROUTER_COOLDOWN_S", "45")

    cfg = Settings(_env_file=None)

    assert cfg.local_llm_base_url == "http://172.17.0.1:8034/v1"
    assert cfg.llm_local_timeout_s == pytest.approx(3.5)
    assert cfg.llm_router_cooldown_s == pytest.approx(45.0)


def test_local_llm_base_url_prefers_tunnel_over_legacy_vllm() -> None:
    cfg = Settings(
        _env_file=None,
        vllm_base_url="http://legacy:8035/v1",
        llm_local_backend_url="http://172.17.0.1:8034/v1/",
    )

    assert cfg.local_llm_base_url == "http://172.17.0.1:8034/v1"


def test_local_llm_base_url_falls_back_to_legacy_vllm() -> None:
    """Пустой `LLM_LOCAL_BACKEND_URL` не ломает старые dev-окружения."""
    cfg = Settings(_env_file=None, vllm_base_url="http://legacy:8035/v1")

    assert cfg.local_llm_base_url == "http://legacy:8035/v1"


async def test_factory_club_uses_tunnel_settings() -> None:
    """Club-роутер берёт URL, таймаут и cooldown из Settings."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    cfg = Settings(
        _env_file=None,
        vllm_base_url="http://127.0.0.1:8034/v1",
        llm_local_backend_url="http://172.17.0.1:8034/v1",
        llm_local_timeout_s=3.5,
        llm_router_cooldown_s=45.0,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_api_key="test-deepseek-key",
    )
    llm = get_llm_client("club", cfg, client=client)

    assert isinstance(llm, BackendRouter)
    assert llm.cooldown_s == pytest.approx(45.0)
    assert llm.primary.timeout_s == pytest.approx(3.5)
    assert llm.primary.endpoint == "http://172.17.0.1:8034/v1/chat/completions"
    assert llm.fallback is not None
    assert (
        llm.fallback.endpoint
        == "https://api.deepseek.com/chat/completions"
    )

    result = await llm.complete("привет")

    assert result.usage.provider == "lmstudio"
    assert str(captured[0].url) == "http://172.17.0.1:8034/v1/chat/completions"

    await client.aclose()


async def test_factory_beginner_uses_tunnel_settings() -> None:
    """`beginner` тоже ходит через туннель: иначе на VPS локальной модели нет."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_completion())

    client = _mock_client(handler)
    cfg = Settings(
        _env_file=None,
        llm_local_backend_url="http://172.17.0.1:8034/v1",
    )
    llm = get_llm_client("beginner", cfg, client=client)

    await llm.generate("привет")

    assert str(captured[0].url) == "http://172.17.0.1:8034/v1/chat/completions"

    await client.aclose()
