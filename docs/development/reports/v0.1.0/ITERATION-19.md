# Итерация 19 — Semantic Cache и LoRA (T-032, T-104, KI-033)

**Дата:** 2026-10-29
**Статус:** ✅ T-032/T-104 в объёме итерации; KI-033 закрыт.

## 1. Файлы (+/-)

### Созданы

- `app/cache/__init__.py`, `app/cache/semantic.py` — exact/semantic lookup,
  `store`, `cleanup_expired_cache`, `fen_hash`, `prompt_hash`,
  `commentary_position_fen`, `chat_position_fen`, `cache_usage`.
- `alembic/versions/a5b6c7d8e9f0_add_semantic_cache.py` — таблица
  `semantic_cache` + HNSW-индекс.
- `scripts/export_dataset.py` — JSONL из `edits` (T-104/T-034).
- `scripts/train_lora.py` — QLoRA-каркас + `--dry-run` (T-104).
- `scripts/__init__.py` — импорт скриптов в тестах.
- `tests/integration/test_semantic_cache.py` (5),
  `test_cache_integration.py` (3), `tests/unit/test_dataset_export.py`
  (1), `test_train_lora.py` (3), `test_lora_config.py` (3).
- `docs/development/reports/v0.1.0/ITERATION-19.md`,
  `ITERATION-19_cache_smoke.log`, `ITERATION-19_lora_smoke.log`,
  `ITERATION-19_prompt_audit_smoke.log`.

### Изменены

- `app/db/models.py` — модель `SemanticCache`.
- `app/config.py` — `semantic_cache_*`, `lora_adapter_path`,
  `lora_model` property.
- `app/llm/factory.py` — локальный клиент использует `cfg.lora_model`.
- `app/commentary/service.py` — lookup/store вокруг генерации.
- `app/commentary/streaming.py` — кэш-ветка + store, чанки из кэша.
- `app/chat/service.py` — lookup/store, commit кэша.
- `app/chat/streaming.py` — кэш-ветка + store.
- `app/streaming.py` — `chunk_text`.
- `app/main.py` — чистка TTL при старте.
- `app/admin.py` — KI-033: аудит в `after_edit` (та же транзакция).
- `scripts/evaluate_llm.py` — semantic cache выключен в eval.
- `tests/conftest.py` — `SEMANTIC_CACHE_ENABLED=false` по умолчанию.
- `tests/unit/test_prompt_admin.py` — тесты атомарного аудита.
- `tests/test_models.py` — `semantic_cache` в метаданных.
- `TODO.md`, `KNOWN_ISSUES.md`, `SPEC.md`, `ARCHITECTURE.md`,
  `DESIGN_ADMIN_AND_OPS.md`, `CHANGELOG.md`, `README.md`, `RELEASES.md`.

## 2. Покрытие

| Область | Тесты | Что проверяют |
| :--- | :--- | :--- |
| Cache service | `tests/integration/test_semantic_cache.py` (5) | exact, semantic, разные prompt/level, UPSERT, TTL+cleanup |
| Cache integration | `tests/integration/test_cache_integration.py` (3) | второй комментарий/чат/SSE не вызывает LLM |
| Dataset export | `tests/unit/test_dataset_export.py` (1) | формат `messages` + метаданные JSONL |
| LoRA validation | `tests/unit/test_train_lora.py` (3) | валидный/битый/отсутствующий JSONL |
| LoRA config | `tests/unit/test_lora_config.py` (3) | `LORA_ADAPTER_PATH` → basename, фабрика клиента |
| Atomic audit | `tests/unit/test_prompt_admin.py` (+2) | аудит в той же сессии; сбой аудита propagate |
| Схема | `tests/test_models.py` (+1) | `semantic_cache` vector(768) и NOT NULL |

Итого: **+18 тестов** к 257 → **275 passed, 1 xfailed**.

## 3. Результаты

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Lint | `ruff check .` | **0** ошибок |
| Types | `mypy app` | **0** ошибок, 58 файлов |
| Tests | `pytest -q` | **275 passed, 1 xfailed** (187.47 с) |
| Build | backend | **0/0** (ruff/mypy); фронтенд не менялся |
| Migration | `alembic upgrade head` | `f4a5b6c7d8e9 → a5b6c7d8e9f0` |
| Drift | `alembic check` | «No new upgrade operations detected» |

Таблиц в БД: **11** (добавлена `semantic_cache`). Head: `a5b6c7d8e9f0`.

## 4. Smoke

### Semantic Cache: два одинаковых запроса

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-19_cache_smoke.log`:

```text
POST /v1/analyze -> 200
semantic_cache_miss
llm_completed ... elapsed_ms=42564 tokens_in=1618 tokens_out=475
semantic_cache_stored
first /v1/commentary -> 200, 43.54 s, model=qwen/qwen3-4b-2507
semantic_cache_hit kind=exact
second /v1/commentary -> 200, 0.030 s
second model=semantic-cache, tokens_used=0, validated=True
speedup: 1440x, under_100ms=True
```

### LoRA dataset + dry-run

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-19_lora_smoke.log`:

```text
Экспортировано записей: 1
JSONL: {"messages":[{"role":"user",...},{"role":"assistant",...}],
        "id":11,"level":"gm","model":"deepseek-flash","schema_version":1}
Записей: 1, ошибок: 0
DRY-RUN OK: формат JSONL корректен, обучение не запускалось.
```

### Атомарный аудит правки промпта

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-19_prompt_audit_smoke.log`:

```text
POST /admin/prompt/edit?pk=2 -> 303
after: version=2 (+1), marker=True
audit: action=prompt.update, target=2
chain: ok=True, checked=19, errors=0
```

## 5. Что не так

- **Semantic cache по FEN — приблизительный.** Для комментария ключ — FEN
  первого ключевого момента, а `prompt_hash` не содержит ходов партии.
  Другая партия с похожей позицией и тем же уровнем/стилем может
  получить чужой текст; exact-хит безопасен, semantic — эвристика
  (`KI-034`). Для чата ключ — финальная позиция + вопрос, риск ниже, но
  сохраняется.
- **Кэш-хит в API неотличим от генерации**, кроме `model=semantic-cache`
  и `tokens_used=0`; отдельного `cached`-флага в ответе нет. Каждый
  кэш-хит комментария создаёт новую строку `commentaries` с нулевыми
  токенами — история растёт.
- **Нет Redis hot-слоя, метрик hit/miss, engine-cache, `cache=off`**
  (`T-032b`). TTL-чистка физически выполняется при старте; lookup
  фильтрует старые записи, но долгоживущий процесс без рестарта может
  накапливать их до следующего старта.
- **Порог 0.05** консервативен: на nomic-embed семантические хиты будут
  редкими; это осознанный выбор против ложных срабатываний.
- **LoRA не обучен.** `train_lora.py` — каркас: реальный запуск требует
  GPU, `torch/transformers/peft/datasets/bitsandbytes`, подходящего
  Qwen-чекпоинта и времени; `--dry-run` проверяет только формат.
  Интеграция предполагает vLLM с `--lora-modules` и basename adapter;
  LM Studio динамические адаптеры через OpenAI API не поддерживает.
- **Dataset export частичный.** Только `edits`, без PII-редакции,
  checksum, аудита выгрузки, админ-страницы и фоновых задач (`T-034`).
  В выгрузке могут быть имена игроков/редакторов.
- **KI-033 закрыт для промпта**, но аудит injection по-прежнему
  best-effort в отдельной транзакции: запрос завершается 422, откатывать
  нечего.
- **CI.** GitHub Actions не запускался (нет push); локально `ruff`,
  `mypy`, `pytest`, `alembic check` зелёные.

## 6. Новые KI

- **KI-034** · Open · Med · cache, llm — semantic-хит по близкому FEN
  может переиспользовать комментарий другой партии (текст может
  упоминать чужие ходы). Exact-хит безопасен; semantic — эвристика.
- **KI-033** — Closed: аудит правки промпта в той же транзакции.
- **KI-032** — Closed (итерация 18), **KI-031** — Closed (итерация 18).

## 7. Обоснование

- **Порог 0.05.** Косинусное расстояние для L2-нормализованных
  эмбеддингов nomic-embed: 0.05 — строгий порог (≈0.998 сходства),
  выбран против ложных semantic-хитов, которые вернули бы текст другой
  партии. Exact-поиск идёт первым и не требует эмбеддера; semantic —
  только при промахе exact. Если понадобится больше хитов, порог
  настраивается `SEMANTIC_CACHE_SIMILARITY_THRESHOLD`, но ценой риска.
- **`prompt_hash`.** В ключ входят уровень, модель, системный промпт,
  style/question: смена промпта или вопроса автоматически делает старые
  записи недостижимыми, без ручной инвалидации. Это компенсирует
  отсутствие Redis Pub/Sub-инвалидации для кэша.
- **Схема/индекс.** `vector(768)` + HNSW (cosine) — быстрый ANN-поиск на
  малых объёмах; уникальный ключ `(fen_hash, level, prompt_hash)` и
  `ON CONFLICT DO UPDATE` не дают дублей при повторной генерации.
- **LoRA-пайплайн.** `export_dataset.py` отдаёт chat-формат
  (`user`/`assistant`), который понимает `apply_chat_template` Qwen;
  `train_lora.py` использует QLoRA (4-bit base + LoRA r=16/alpha=32) —
  минимальный бюджет GPU. `LORA_ADAPTER_PATH` → basename как имя
  adapter-модели: vLLM принимает adapter по имени, если запущен с
  `--lora-modules name=path`; это явный контракт, а не «магия».
- **Атомарный аудит.** `PromptAdmin.after_edit` вызывается до commit и
  получает `request.state.session`; `append_audit` пишет в ту же
  транзакцию. При сбое аудита исключение уходит в `handle_exception`,
  ответ 4xx, `DBSessionMiddleware` откатывает и `prompts`, и
  `audit_logs` — KI-033 закрыт без outbox/компенсаций.
