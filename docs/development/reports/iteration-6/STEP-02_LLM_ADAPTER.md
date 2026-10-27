# STEP-02 — LLM-адаптер (T-013)

**Дата:** 2026-10-28
**Итерация:** 6 — API + LLM
**Статус:** ✅ выполнен

---

## 1. Архитектура

```
app/llm/
├── base.py           BaseLLMClient (generate / complete / aclose),
│                     LLMResult, LLMUsage
├── openai_client.py  OpenAICompatibleClient — один клиент на все
│                     OpenAI-совместимые API (LM Studio, DeepSeek, OpenAI)
└── factory.py        get_llm_client(level) + resolve_llm_provider(level)
```

`BaseLLMClient`:

* `generate(prompt, system_prompt) -> str` — контракт из постановки;
* `complete(...) -> LLMResult` — текст + `LLMUsage(tokens_in, tokens_out,
  provider, model)` для T-016 (колонки `commentaries.tokens_*`);
* `aclose()` + async-context-manager.

Почему два метода: постановка просит `generate -> str`, но T-016 обязан
писать токены в БД. `complete` — надстройка, `generate` — удобный фасад.

## 2. Клиент

Один класс на все провайдеры, потому что протокол одинаковый:
`POST {base_url}/chat/completions`.

| Аспект | Реализация |
| :--- | :--- |
| HTTP | `httpx.AsyncClient`, `httpx.Timeout(timeout_s)` |
| Ключ | `Authorization: Bearer <key>`; для LM Studio — заглушка `lm-studio` |
| Таймаут | `LLMTimeoutError`, без ретраев (повтор таймаута только удлиняет ожидание) |
| 429 / 5xx | ретрай с экспоненциальной паузой, `Retry-After` учитывается (≤ 30 с) |
| Прочие 4xx | сразу `LLMError` (ключ/модель/запрос — повторять бессмысленно) |
| Разбор | `choices[0].message.content`, `usage.prompt_tokens/completion_tokens` |
| Логи | провайдер, модель, токены, время — **без текста промпта** (приватность партий) |

`max_tokens` по умолчанию 512 (`llm_max_tokens`): без ограничения LM Studio
генерирует «до конца», и короткий запрос может идти минутами.

## 3. Маршрутизация `get_llm_client(level)`

| Уровень | Провайдер | Модель | Ключ |
| :--- | :--- | :--- | :--- |
| `beginner` | LM Studio | `vllm_model` (`qwen/qwen3-4b-2507`) | `vllm_api_key` (`lm-studio`) |
| `club` | LM Studio | то же | то же |
| `gm` | DeepSeek | `deepseek_model` (`deepseek-chat`) | `deepseek_api_key` (обязателен) |

* Новый уровень → `InvalidLevelError` (422).
* Нет ключа DeepSeek → `LLMNotConfiguredError` (503) — **не** молчаливое
  переключение на локальную модель: для «гроссмейстера» качество важнее.
* Инъекция `httpx.AsyncClient` — для тестов с `MockTransport`.

## 4. Конфигурация

```python
vllm_base_url: str = "http://127.0.0.1:8034/v1"   # LM Studio
vllm_model: str = "qwen/qwen3-4b-2507"
vllm_api_key: str = "lm-studio"
deepseek_base_url: str = "https://api.deepseek.com"
deepseek_model: str = "deepseek-chat"
deepseek_api_key: str | None = None
llm_max_tokens: int = 512
llm_timeout_s: float = 60.0
llm_max_retries: int = 2
llm_retry_backoff_s: float = 0.5
```

Порт **8034** (а не 1234) — фактическая настройка LM Studio на этой машине,
подтверждена запросом `/v1/models`. Имена `vllm_*` сохранены как
исторические, хотя сервер теперь LM Studio.

## 5. Тесты (13, без сети)

`httpx.MockTransport` эмулирует провайдера:

* разбор ответа + `usage` + заголовок `Authorization` + URL;
* system prompt идёт первым сообщением;
* 429 → ретрай → 200 (2 запроса);
* 500 → исчерпание ретраев → `LLMError` (2 запроса);
* 401 → без ретраев (1 запрос);
* таймаут → `LLMTimeoutError`;
* кривой JSON → `LLMError`;
* валидация аргументов клиента (`model`, `base_url`, `max_retries`);
* фабрика: `club`/`beginner` → `lmstudio` + `qwen/qwen3-4b-2507` +
  `Bearer lm-studio`; `gm` → `deepseek` + `deepseek-chat` + ключ;
* фабрика: нет ключа → `LLMNotConfiguredError`; неизвестный уровень →
  `InvalidLevelError`.

## 6. Что дальше

Шаг 3 — живые тесты на реальных провайдерах.
