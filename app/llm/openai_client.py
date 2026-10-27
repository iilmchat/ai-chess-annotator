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
import time
from typing import Any

import httpx
import structlog

from app.errors import LLMError, LLMTimeoutError
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage

__all__ = ["OpenAICompatibleClient"]

logger = structlog.get_logger(__name__)

#: Путь chat-completions относительно `base_url`.
_CHAT_PATH = "/chat/completions"

#: Максимальная пауза из `Retry-After`, которую мы готовы ждать.
_MAX_RETRY_AFTER_S = 30.0


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
        response = await self._post_with_retries(payload)
        result = self._parse(response)
        logger.info(
            "llm_completed",
            provider=self.provider,
            model=result.usage.model,
            tokens_in=result.usage.tokens_in,
            tokens_out=result.usage.tokens_out,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        return result

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
            self._owns_client = False

    # --- внутреннее -------------------------------------------------------

    def _build_payload(
        self, prompt: str, system_prompt: str | None
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
        usage = LLMUsage(
            provider=self.provider,
            model=str(data.get("model") or self.model),
            tokens_in=int(usage_raw.get("prompt_tokens") or 0),
            tokens_out=int(usage_raw.get("completion_tokens") or 0),
        )
        return LLMResult(text=str(text).strip(), usage=usage)
