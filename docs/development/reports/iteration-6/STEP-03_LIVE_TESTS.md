# STEP-03 — Живые тесты LLM (LM Studio и DeepSeek)

**Дата:** 2026-10-28
**Итерация:** 6 — API + LLM
**Статус:** ✅ выполнен (оба провайдера ответили)

---

## 1. Окружение

| Компонент | Значение |
| :--- | :--- |
| LM Studio | `http://127.0.0.1:8034/v1` (порт 8034, не 1234) |
| Модель LM Studio | `qwen/qwen3-4b-2507` |
| DeepSeek | `https://api.deepseek.com`, модель `deepseek-chat` |
| Ключ DeepSeek | задан в локальном `.env` (в отчёт и логи не попадает) |

Проверка до кода: `GET /v1/models` у LM Studio вернул 200 и список моделей;
`GET https://api.deepseek.com/models` с ключом — 200.

## 2. Выбор локальной модели (важный замер)

`qwen3-8b` — thinking-модель. Факты с этого хоста:

| Модель | `max_tokens` | Время | `completion_tokens` | `content` |
| :--- | ---: | ---: | ---: | :--- |
| `qwen3-8b` | 120 | 41 с | 120 | **пусто** (всё ушло в reasoning) |
| `qwen3-8b` | 300 | 157 с | 300 | **пусто** |
| `qwen3-8b` | — | > 180 с | — | таймаут |
| `qwen/qwen3-4b-2507` | 100 | **0.85–1.3 с** (тёплый) | 14 | «Привет, шахматы! 😊♟️» |
| `qwen/qwen3-4b-2507` | 100 | ≈ 12 с (первый, загрузка модели) | 14 | то же |

Вывод: для интерактивных ответов локальный дефолт — `qwen/qwen3-4b-2507`
(non-thinking). `qwen3-8b` остаётся вариантом «умнее, но медленнее»;
холодный старт зафиксирован в KI-022, отсутствие расчёта стоимости — в
KI-021.

## 3. Живой тест через наш адаптер

Скрипт (запускается вручную; в pytest есть `tests/integration/test_llm_live.py`):

```python
for level in ("club", "gm"):
    client = get_llm_client(level)
    result = await client.complete("Скажи 'Привет, шахматы!'")
```

Результат:

```
local base_url: http://127.0.0.1:8034/v1 | model: qwen/qwen3-4b-2507
deepseek base_url: https://api.deepseek.com | model: deepseek-chat
--- level=club provider=lmstudio model=qwen/qwen3-4b-2507
    usage: model=qwen/qwen3-4b-2507 tokens_in=21 tokens_out=14 elapsed=1741 ms
    text(20 chars): Привет, шахматы! 😊♟️
--- level=gm provider=deepseek model=deepseek-chat
    usage: model=deepseek-flash tokens_in=16 tokens_out=8 elapsed=1605 ms
    text(16 chars): Привет, шахматы!
```

| Уровень | Провайдер | Фактическая модель | Токены (in/out) | Время | Ответ |
| :--- | :--- | :--- | :--- | ---: | :--- |
| `club` | LM Studio | `qwen/qwen3-4b-2507` | 21 / 14 | 1741 мс | «Привет, шахматы! 😊♟️» |
| `gm` | DeepSeek | `deepseek-chat` → `deepseek-flash` | 16 / 8 | 1605 мс | «Привет, шахматы!» |

Замечание: DeepSeek вернул в поле `model` имя `deepseek-flash` — это
серверный алиас `deepseek-chat`; адаптер пишет в `usage.model` то, что
ответил сервер, поэтому в метриках будет фактическая модель.

## 4. Тесты в репозитории

`tests/integration/test_llm_live.py` — 2 теста:

* `test_live_club_uses_lmstudio` — скип, если `/v1/models` LM Studio не
  отвечает;
* `test_live_gm_uses_deepseek` — скип, если `DEEPSEEK_API_KEY` пуст.

В CI (без LM Studio и ключа) оба скипаются — `pytest -q` остаётся зелёным.
Локально: **2 passed за 4.89 с**.

## 5. Что дальше

Шаг 4 — прогон, документация, коммит.
