"""OpenAI-совместимый клиент: LM Studio, DeepSeek, OpenAI (T-013).

Все три провайдера принимают один и тот же протокол
`POST {base_url}/chat/completions`, поэтому реализация одна, а разница —
в `base_url`, `api_key` и имени модели.

Особенности:

* **LM Studio** (`http://127.0.0.1:8034/v1`) не проверяет ключ, но клиенты
  OpenAI-формата всё равно шлют `Authorization` — передаём заглушку
  `lm-studio`, чтобы не ловить 401 на строгих сборках прокси.
* **DeepSeek** (`https://api.deepseek.com`) — тот же путь `/chat/completions`;
  ключ обязателен.
* **Ретраи**: 429 и 5xx повторяются с экспоненциальной паузой; `Retry-After`
  учитывается. Таймаут и «странный ответ» — сразу ошибка, без повторов
  (повтор таймаута только увеличивает задержку).
* **Приватность**: промпт может содержать партию, поэтому в логи уходят
  только провайдер, модель, число токенов и время — не текст.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from app.errors import LLMError, LLMTimeoutError
from app.finops import compute_cost_usd, record_token_usage
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.llm.calls import CallStatus, record_llm_call
from app.llm.tokens import estimate_tokens
from app.observability import get_tracer

__all__ = ["OpenAICompatibleClient"]

logger = structlog.get_logger(__name__)

#: Путь chat-completions относительно `base_url`.
_CHAT_PATH = "/chat/completions"

#: Максимальная пауза из `Retry-After`, которую мы готовы ждать.
_MAX_RETRY_AFTER_S = 30.0


def _status_for_error(exc: BaseException) -> tuple[CallStatus, str]:
    """Статус вызова для `llm_calls` по исключению клиента.

    Отдельный `timeout` важен для LLMOps: таймауты считаются иначе, чем
    прочие ошибки (SLA провайдера против «неверного ответа»).
    """
    if isinstance(exc, LLMTimeoutError):
        return "timeout", "llm_timeout"
    if isinstance(exc, LLMError):
        return "error", exc.code
    return "error", type(exc).__name__


class OpenAICompatibleClient(BaseLLMClient):
    """Клиент любого OpenAI-совместимого API.

    Args:
        base_url: база API (для LM Studio — с `/v1`, для DeepSeek — без).
        model: имя модели.
        api_key: токен; для LM Studio — заглушка `lm-studio`.
        provider_name: имя провайдера для логов (`lmstudio`, `deepseek`).
        timeout_s: таймаут одного запроса.
        max_retries: повторы при 429/5xx и сетевых ошибках.
        retry_backoff_s: базовая пауза перед повтором.
        client: готовый `httpx.AsyncClient` (для тестов с `MockTransport`).
        temperature: температура генерации.
        max_tokens: ограничение ответа (None — решает сервер).
        price_in_usd_per_1m: цена входных токенов (для `llm.cost_usd`).
        price_out_usd_per_1m: цена выходных токенов.
        tracer: трейсер OTel (тесты подставляют свой провайдер).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        provider_name: str = "openai-compatible",
        timeout_s: float = 60.0,
        max_retries: int = 2,
        retry_backoff_s: float = 0.5,
        client: httpx.AsyncClient | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        price_in_usd_per_1m: float = 0.0,
        price_out_usd_per_1m: float = 0.0,
        tracer: Tracer | None = None,
    ) -> None:
        super().__init__(
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_backoff_s=retry_backoff_s,
        )
        if not base_url:
            raise ValueError("base_url не может быть пустым")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._provider_name = provider_name
        self._client = client
        self._owns_client = client is None
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._price_in = price_in_usd_per_1m
        self._price_out = price_out_usd_per_1m
        self._tracer = tracer or get_tracer()

    @property
    def provider(self) -> str:
        return self._provider_name

    @property
    def endpoint(self) -> str:
        """Полный URL chat-completions (пригодится в логах и тестах)."""
        return f"{self._base_url}{_CHAT_PATH}"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        """Отправить промпт и вернуть ответ с расходом токенов.

        Raises:
            LLMTimeoutError: провайдер не ответил за `timeout_s`.
            LLMError: 4xx (кроме 429), исчерпанные ретраи, кривой ответ.
        """
        payload = self._build_payload(prompt, system_prompt)
        started = time.perf_counter()
        tokens_in = 0
        tokens_out = 0
        cost_usd = 0.0
        status: CallStatus = "ok"
        error_code: str | None = None
        try:
            with self._tracer.start_as_current_span("llm_generate") as span:
                self._set_span_attributes(span)
                try:
                    response = await self._post_with_retries(payload)
                except Exception as exc:
                    status, error_code = _status_for_error(exc)
                    self._record_span_error(span, exc, started)
                    raise
                try:
                    result = self._parse(response)
                except Exception as exc:
                    # Ответ пришёл, но не разобрался — это «invalid», а не
                    # сетевая/HTTP-ошибка.
                    status, error_code = "invalid", "invalid_response"
                    self._record_span_error(span, exc, started)
                    raise

                span.set_attribute("llm.tokens_in", result.usage.tokens_in)
                span.set_attribute("llm.tokens_out", result.usage.tokens_out)
                span.set_attribute("llm.cost_usd", result.usage.cost_usd)
                span.set_attribute("llm.status", "ok")
                span.set_attribute(
                    "llm.latency_ms", self._elapsed_ms(started)
                )
                span.set_status(Status(StatusCode.OK))
                tokens_in = result.usage.tokens_in
                tokens_out = result.usage.tokens_out
                cost_usd = result.usage.cost_usd
                logger.info(
                    "llm_completed",
                    provider=self.provider,
                    model=result.usage.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_usd,
                    elapsed_ms=self._elapsed_ms(started),
                )
                await record_token_usage(
                    tokens_in, tokens_out, cost_usd=cost_usd
                )
                return result
        except asyncio.CancelledError:
            status, error_code = "error", "cancelled"
            raise
        finally:
            # T-030: durable-история LLMOps — метрики без текстов/PII.
            record_llm_call(
                provider=self.provider,
                model=self.model,
                status=status,
                latency_ms=self._elapsed_ms(started),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                error_code=error_code,
                level=self._level,
                cache_hit=False,
            )

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        """Стримить ответ через `stream=true` (SSE OpenAI-формата).

        Отдаёт только текстовые дельты. Токены в stream-ответе могут
        отсутствовать; вызывающая сторона оценивает их эвристикой.

        T-030: при завершении, ошибке **и отмене** (клиент закрыл стрим)
        вызов попадает в `llm_calls` со статусом ok/error/timeout.
        """
        payload = self._build_payload(prompt, system_prompt, stream=True)
        started = time.perf_counter()
        chars = 0
        failed = False
        tokens_in_est = estimate_tokens(prompt) + estimate_tokens(
            system_prompt or ""
        )
        tokens_out_est = 0
        cost_est = 0.0
        status: CallStatus = "ok"
        error_code: str | None = None
        try:
            with self._tracer.start_as_current_span("llm_stream") as span:
                self._set_span_attributes(span)
                try:
                    client = self._http_client()
                    async with client.stream(
                        "POST", self.endpoint, json=payload, headers=self._headers()
                    ) as response:
                        if response.status_code != httpx.codes.OK:
                            body = (await response.aread()).decode(
                                "utf-8", errors="replace"
                            )
                            raise LLMError(
                                f"{self.provider}: HTTP {response.status_code}: "
                                f"{body[:300]}"
                            )
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk["choices"][0].get("delta") or {}
                                content = delta.get("content")
                            except (
                                json.JSONDecodeError,
                                KeyError,
                                IndexError,
                                TypeError,
                            ):
                                continue
                            if isinstance(content, str) and content:
                                chars += len(content)
                                yield content
                except httpx.TimeoutException as exc:
                    failed = True
                    status, error_code = "timeout", "llm_timeout"
                    self._record_span_error(span, exc, started)
                    raise LLMTimeoutError(
                        f"{self.provider}: таймаут стрима {self.timeout_s:.0f} с"
                    ) from exc
                except httpx.HTTPError as exc:
                    failed = True
                    status, error_code = _status_for_error(exc)
                    self._record_span_error(span, exc, started)
                    raise LLMError(
                        f"{self.provider}: сеть недоступна: {exc}"
                    ) from exc
                except asyncio.CancelledError:
                    # Отмена задачи/таймаут выше по стеку: запись обязательна.
                    failed = True
                    status, error_code = "error", "cancelled"
                    raise
                except GeneratorExit:
                    # Потребитель закрыл стрим раньше времени (disconnect).
                    failed = True
                    status, error_code = "error", "client_disconnect"
                    raise
                except Exception as exc:
                    failed = True
                    status, error_code = _status_for_error(exc)
                    self._record_span_error(span, exc, started)
                    raise
                finally:
                    tokens_out_est = max(1, chars // 4) if chars else 0
                    cost_est = compute_cost_usd(
                        tokens_in_est,
                        tokens_out_est,
                        price_in_per_1m=self._price_in,
                        price_out_per_1m=self._price_out,
                    )
                    span.set_attribute("llm.chars_out", chars)
                    span.set_attribute("llm.tokens_in", tokens_in_est)
                    span.set_attribute("llm.tokens_out", tokens_out_est)
                    span.set_attribute("llm.cost_usd", cost_est)
                    if not failed:
                        span.set_attribute("llm.status", "ok")
                        span.set_status(Status(StatusCode.OK))
                    logger.info(
                        "llm_stream_completed",
                        provider=self.provider,
                        model=self.model,
                        chars=chars,
                        tokens_in=tokens_in_est,
                        tokens_out=tokens_out_est,
                        cost_usd=cost_est,
                        elapsed_ms=self._elapsed_ms(started),
                    )
        finally:
            # T-030: запись и при отмене/закрытии стрима (метрики, без текста).
            record_llm_call(
                provider=self.provider,
                model=self.model,
                status=status,
                latency_ms=self._elapsed_ms(started),
                tokens_in=tokens_in_est,
                tokens_out=tokens_out_est,
                cost_usd=cost_est,
                error_code=error_code,
                level=self._level,
                cache_hit=False,
            )
        if not failed:
            await record_token_usage(
                tokens_in_est, tokens_out_est, cost_usd=cost_est
            )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    # --- внутреннее -------------------------------------------------------

    def _set_span_attributes(self, span: Span) -> None:
        """Базовые атрибуты спана: модель, провайдер, уровень (T-030)."""
        span.set_attribute("llm.provider", self.provider)
        span.set_attribute("llm.model", self.model)
        if self._level is not None:
            span.set_attribute("llm.level", self._level)

    def _record_span_error(
        self, span: Span, exc: Exception, started: float
    ) -> None:
        """Отметить ошибку, не кладя в спан текст исключения (privacy)."""
        span.set_attribute("llm.status", "error")
        span.set_attribute("llm.error_type", type(exc).__name__)
        span.set_attribute("llm.latency_ms", self._elapsed_ms(started))
        span.set_status(Status(StatusCode.ERROR, type(exc).__name__))

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _build_payload(
        self,
        prompt: str,
        system_prompt: str | None,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        if stream:
            payload["stream"] = True
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s))
            self._owns_client = True
        return self._client

    async def _post_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        last_status: int | None = None
        for attempt in range(self.max_retries + 1):
            try:
                client = self._http_client()
                response = await client.post(
                    self.endpoint, json=payload, headers=self._headers()
                )
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"{self.provider}: таймаут {self.timeout_s:.0f} с"
                ) from exc
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise LLMError(
                        f"{self.provider}: сеть недоступна: {exc}"
                    ) from exc
                await asyncio.sleep(self._backoff(attempt))
                continue

            if response.status_code == httpx.codes.OK:
                return response
            if response.status_code != httpx.codes.TOO_MANY_REQUESTS and (
                response.status_code < 500
            ):
                raise LLMError(
                    f"{self.provider}: HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

            last_status = response.status_code
            if attempt >= self.max_retries:
                break
            delay = self._retry_delay(attempt, response)
            logger.warning(
                "llm_retry",
                provider=self.provider,
                status=response.status_code,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
            )
            await asyncio.sleep(delay)

        raise LLMError(
            f"{self.provider}: HTTP {last_status} после "
            f"{self.max_retries + 1} попыток"
        )

    def _backoff(self, attempt: int) -> float:
        return self.retry_backoff_s * 2.0**attempt

    def _retry_delay(self, attempt: int, response: httpx.Response) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_RETRY_AFTER_S))
            except ValueError:
                pass
        return self._backoff(attempt)

    def _parse(self, response: httpx.Response) -> LLMResult:
        try:
            data: Any = response.json()
            text = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(
                f"{self.provider}: неожиданный ответ: {response.text[:300]}"
            ) from exc

        usage_raw = data.get("usage") or {}
        tokens_in = int(usage_raw.get("prompt_tokens") or 0)
        tokens_out = int(usage_raw.get("completion_tokens") or 0)
        usage = LLMUsage(
            provider=self.provider,
            model=str(data.get("model") or self.model),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=compute_cost_usd(
                tokens_in,
                tokens_out,
                price_in_per_1m=self._price_in,
                price_out_per_1m=self._price_out,
            ),
        )
        return LLMResult(text=str(text).strip(), usage=usage)
