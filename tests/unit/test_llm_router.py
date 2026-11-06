"""Тесты `BackendRouter`: local-first fallback, circuit-breaker, стрим (T-123).

Сеть не используется: бэкенды — фейковые `BaseLLMClient`, а логи
перехватываются подменой `app.llm.router.logger`. Проверяется, что:

* при ошибке соединения/таймауте локального бэкенда запрос уходит в
  облако, а в лог пишется `local_gpu_unavailable`;
* обычные ошибки ответа (HTTP 4xx, кривой JSON) **не** переключают бэкенд;
* circuit-breaker пропускает мёртвый primary на время cooldown и
  восстанавливается после него;
* стрим переключается только до первого отданного чанка.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.errors import LLMError, LLMTimeoutError
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.llm.router import BackendRouter, is_unavailable_error


class _Recorder:
    """Минимальный structlog-логгер для перехвата событий."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def warning(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def names(self) -> list[str]:
        return [event for event, _ in self.events]

    def fields(self, name: str) -> dict[str, Any]:
        for event, payload in self.events:
            if event == name:
                return payload
        raise AssertionError(f"событие {name!r} не залогировано: {self.names()}")


class _Clock:
    """Управляемые монотонные часы."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeClient(BaseLLMClient):
    """Фейковый бэкенд: пишет вызовы, умеет падать и стримить."""

    def __init__(
        self,
        *,
        name: str = "fake",
        text: str = "ответ",
        error: BaseException | None = None,
        stream: list[str | BaseException] | None = None,
    ) -> None:
        super().__init__(model=f"{name}-model", timeout_s=1.0)
        self.name = name
        self.text = text
        self.error = error
        self.stream = stream
        self.calls = 0
        self.stream_calls = 0
        self.levels: list[str | None] = []
        self.closed = False

    @property
    def provider(self) -> str:
        return self.name

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return LLMResult(
            text=self.text,
            usage=LLMUsage(
                provider=self.name,
                model=self.model,
                tokens_in=3,
                tokens_out=2,
            ),
        )

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        if self.error is not None:
            raise self.error
        for item in self.stream or [self.text]:
            if isinstance(item, BaseException):
                raise item
            yield item

    def set_level(self, level: str | None) -> None:
        super().set_level(level)
        self.levels.append(level)

    async def aclose(self) -> None:
        self.closed = True


def _router(
    primary: BaseLLMClient,
    fallback: BaseLLMClient | None = None,
    *,
    cooldown_s: float = 60.0,
    clock: _Clock | None = None,
) -> BackendRouter:
    backends = [primary]
    if fallback is not None:
        backends.append(fallback)
    return BackendRouter(
        backends, cooldown_s=cooldown_s, clock=clock or _Clock()
    )


def _wrapped_connect_error() -> LLMError:
    """Ошибка в том виде, в каком её отдаёт OpenAICompatibleClient."""

    def _raise() -> None:
        try:
            raise httpx.ConnectError("connection refused")
        except httpx.ConnectError as exc:
            raise LLMError("lmstudio: сеть недоступна") from exc

    try:
        _raise()
    except LLMError as exc:
        return exc
    raise AssertionError("недостижимо")


# --- is_unavailable_error ----------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("refused"), True),
        (httpx.ConnectTimeout("slow"), True),
        (httpx.ReadTimeout("slow"), True),
        (LLMTimeoutError("timeout"), True),
        (LLMError("lmstudio: HTTP 400"), False),
        (LLMError("lmstudio: неожиданный ответ"), False),
        (ValueError("boom"), False),
    ],
)
def test_is_unavailable_error_matrix(
    exc: BaseException, expected: bool
) -> None:
    assert is_unavailable_error(exc) is expected


def test_is_unavailable_error_checks_cause_chain() -> None:
    assert is_unavailable_error(_wrapped_connect_error()) is True


# --- complete ----------------------------------------------------------------


async def test_primary_success_does_not_touch_fallback() -> None:
    primary = _FakeClient(name="lmstudio", text="локальный ответ")
    fallback = _FakeClient(name="deepseek", text="облачный ответ")
    router = _router(primary, fallback)

    result = await router.complete("промпт")

    assert result.text == "локальный ответ"
    assert result.usage.provider == "lmstudio"
    assert fallback.calls == 0
    assert router.provider == "lmstudio"
    assert router.model == "lmstudio-model"
    assert router.backends == (primary, fallback)
    assert router.primary is primary
    assert router.fallback is fallback


async def test_connect_error_falls_back_to_cloud_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.router.logger", recorder)
    primary = _FakeClient(name="lmstudio", error=httpx.ConnectError("refused"))
    fallback = _FakeClient(name="deepseek", text="облачный ответ")
    router = _router(primary, fallback)

    result = await router.complete("промпт")

    assert result.text == "облачный ответ"
    assert result.usage.provider == "deepseek"
    assert primary.calls == 1
    assert fallback.calls == 1
    fields = recorder.fields("local_gpu_unavailable")
    assert fields["primary"] == "lmstudio"
    assert fields["fallback"] == "deepseek"
    assert fields["error"] == "ConnectError"
    assert fields["cooldown_s"] == 60.0


async def test_timeout_falls_back_to_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.router.logger", recorder)
    primary = _FakeClient(name="lmstudio", error=httpx.ReadTimeout("slow"))
    fallback = _FakeClient(name="deepseek")

    result = await _router(primary, fallback).complete("промпт")

    assert result.usage.provider == "deepseek"
    assert recorder.fields("local_gpu_unavailable")["error"] == "ReadTimeout"


async def test_wrapped_connect_error_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.router.logger", recorder)
    primary = _FakeClient(name="lmstudio", error=_wrapped_connect_error())
    fallback = _FakeClient(name="deepseek")

    result = await _router(primary, fallback).complete("промпт")

    assert result.usage.provider == "deepseek"
    assert recorder.fields("local_gpu_unavailable")["error"] == "LLMError"


async def test_http_error_does_not_fall_back() -> None:
    primary = _FakeClient(name="lmstudio", error=LLMError("lmstudio: HTTP 400"))
    fallback = _FakeClient(name="deepseek")
    router = _router(primary, fallback)

    with pytest.raises(LLMError, match="HTTP 400"):
        await router.complete("промпт")

    assert fallback.calls == 0


async def test_both_backends_fail_raises_fallback_error() -> None:
    primary = _FakeClient(name="lmstudio", error=httpx.ConnectError("refused"))
    fallback = _FakeClient(name="deepseek", error=LLMError("deepseek: HTTP 500"))
    router = _router(primary, fallback)

    with pytest.raises(LLMError, match="HTTP 500"):
        await router.complete("промпт")

    assert fallback.calls == 1


async def test_single_backend_raises_without_fallback() -> None:
    router = _router(_FakeClient(error=httpx.ConnectError("refused")))

    with pytest.raises(httpx.ConnectError):
        await router.complete("промпт")


# --- circuit-breaker ---------------------------------------------------------


async def test_circuit_open_skips_primary_within_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.router.logger", recorder)
    clock = _Clock()
    primary = _FakeClient(name="lmstudio", error=httpx.ConnectError("refused"))
    fallback = _FakeClient(name="deepseek")
    router = _router(primary, fallback, cooldown_s=60.0, clock=clock)

    await router.complete("первый")
    await router.complete("второй")

    assert primary.calls == 1  # второй запрос не трогал мёртвый GPU
    assert fallback.calls == 2
    assert "llm_router_circuit_open" in recorder.names()


async def test_circuit_recovers_after_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.router.logger", recorder)
    clock = _Clock()
    primary = _FakeClient(name="lmstudio", error=httpx.ConnectError("refused"))
    fallback = _FakeClient(name="deepseek")
    router = _router(primary, fallback, cooldown_s=60.0, clock=clock)

    await router.complete("первый")
    clock.advance(61.0)
    primary.error = None

    result = await router.complete("второй")

    assert result.usage.provider == "lmstudio"
    assert primary.calls == 2
    assert "llm_router_primary_recovered" in recorder.names()


async def test_zero_cooldown_retries_primary_every_time() -> None:
    clock = _Clock()
    primary = _FakeClient(name="lmstudio", error=httpx.ConnectError("refused"))
    fallback = _FakeClient(name="deepseek")
    router = _router(primary, fallback, cooldown_s=0.0, clock=clock)

    await router.complete("раз")
    await router.complete("два")

    assert primary.calls == 2


# --- stream ------------------------------------------------------------------


async def test_stream_falls_back_before_first_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("app.llm.router.logger", recorder)
    primary = _FakeClient(name="lmstudio", error=httpx.ConnectError("refused"))
    fallback = _FakeClient(name="deepseek", stream=["об", "лачный"])
    router = _router(primary, fallback)

    chunks = [chunk async for chunk in router.stream_generate("промпт")]

    assert chunks == ["об", "лачный"]
    assert "local_gpu_unavailable" in recorder.names()


async def test_stream_partial_failure_is_not_duplicated() -> None:
    primary = _FakeClient(
        name="lmstudio",
        stream=["начало ответа", httpx.ConnectError("refused")],
    )
    fallback = _FakeClient(name="deepseek", stream=["облако"])
    router = _router(primary, fallback)

    chunks: list[str] = []
    with pytest.raises(httpx.ConnectError):
        async for chunk in router.stream_generate("промпт"):
            chunks.append(chunk)

    assert chunks == ["начало ответа"]
    assert fallback.stream_calls == 0


# --- служебное ---------------------------------------------------------------


async def test_set_level_is_forwarded_to_all_backends() -> None:
    primary = _FakeClient(name="lmstudio")
    fallback = _FakeClient(name="deepseek")
    router = _router(primary, fallback)

    router.set_level("club")

    assert router.level == "club"
    assert primary.levels == ["club"]
    assert fallback.levels == ["club"]


async def test_aclose_closes_all_backends() -> None:
    primary = _FakeClient(name="lmstudio")
    fallback = _FakeClient(name="deepseek")
    router = _router(primary, fallback)

    await router.aclose()

    assert primary.closed is True
    assert fallback.closed is True


def test_empty_backends_rejected() -> None:
    with pytest.raises(ValueError, match="хотя бы один"):
        BackendRouter([])


def test_negative_cooldown_rejected() -> None:
    with pytest.raises(ValueError, match="cooldown"):
        BackendRouter([_FakeClient()], cooldown_s=-1.0)
