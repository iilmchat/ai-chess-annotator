# STEP-02 — Прогрев LLM при старте (KI-022)

**Дата:** 2026-10-28
**Итерация:** 7 — Промпты и валидатор
**Статус:** ✅ выполнен

---

## 1. Проблема

LM Studio грузит веса при первом обращении: первый запрос ≈ **12 с**,
тёплый — ≈ 1 с. Для API это означало бы, что первый пользователь после
перезапуска ждёт лишние 12 секунд (и может получить таймаут, если тот
маленький).

## 2. Решение

`app/llm/warmup.py`:

```python
async def warmup_local_llm(settings=None, *, client=None) -> bool
```

* отправляет короткий промпт `«Ответь одним словом: ok»` в `get_llm_client("club")`
  (локальный LM Studio);
* **best-effort**: любая ошибка (LM Studio не запущена, таймаут, 4xx)
  логируется как `llm_warmup_failed` и не пробрасывается — приложение
  стартует в любом случае;
* закрывает свой клиент в `finally`, чужой (переданный в тестах) — нет.

`app/main.py`, lifespan:

```python
warmup_task = asyncio.create_task(warmup_local_llm(cfg), name="llm-warmup")
app.state.llm_warmup = warmup_task
...
finally:
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
        with suppress(asyncio.CancelledError):
            await warmup_task
```

Настройка `llm_warmup_enabled` (по умолчанию `True`) позволяет выключить
прогрев — интеграционные тесты API делают именно так, чтобы не занимать
LM Studio фоновой загрузкой.

## 3. Проверки

Живой запуск приложения (TestClient с lifespan):

```
app_started engine_pool_size=4 env=dev llm_warmup=True rate_limit=True
llm_completed elapsed_ms=933 model=qwen/qwen3-4b-2507 provider=lmstudio tokens_in=16 tokens_out=4
llm_warmup_ok elapsed_ms=934 provider=lmstudio
warmup done: True
warmup result: True
app_stopped
```

Юнит-тесты (`tests/unit/test_warmup.py`, 3):

| Тест | Что проверяет |
| :--- | :--- |
| `test_warmup_sends_short_prompt` | отправлен ровно `WARMUP_PROMPT`, внешний клиент не закрыт |
| `test_warmup_swallows_provider_errors` | `LLMError` не пробрасывается, результат `False` |
| `test_warmup_closes_owned_client` | собственный клиент закрывается в `finally` |

## 4. Остаточный риск (в KI-022)

Если LM Studio **не запущена на момент старта**, прогрев упадёт в лог, и
первый реальный запрос после её запуска снова будет холодным. Полностью
это решается только повтором прогрева/keep-alive — не делаем, пока не
появится эндпоинт комментария (T-016).

## 5. Что дальше

Шаг 3 — live E2E: пакет фактов → LM Studio → валидатор.
