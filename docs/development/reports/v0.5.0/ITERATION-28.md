# Итерация 28 — FinOps Durable и Admin Export (T-031b, T-034b, KI-046, KI-047)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-046 и KI-047 закрыты**; **T-031b и T-034b
закрыты в объёме MVP** (месячные лимиты/алерты и фоновая выгрузка —
KI-049/KI-050). Заведены KI-049, KI-050.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### FinOps durable-агрегаты (T-031b)

| Файл | Что изменилось |
| :--- | :--- |
| `alembic/versions/e9f0a1b2c3d4_add_usage_daily.py` *(новый)* | Миграция `usage_daily` (down_revision `d8e9f0a1b2c3`, head): `subject_type` varchar(8), `subject_key` varchar(64), `day` DATE, `tokens_in/out` BIGINT, `cost_usd` NUMERIC(12,6), `requests` INTEGER, created/updated; `UNIQUE(subject_type, subject_key, day)` = `uq_usage_daily_subject_day`, индексы `(day, subject_type)` и `(subject_type, subject_key, day)`, CHECK ≥ 0; upgrade+downgrade |
| `app/db/models.py` | Модель `UsageDaily` (те же колонки/ограничения, docstring с назначением durable-слоя) |
| `app/finops.py` | `utc_date()`, `parse_subject()` (`user:42`/`ip:…` → type/key), `usage_daily_tokens()` (SELECT за день, ошибки → None), `_upsert_usage_daily()` (`INSERT … ON CONFLICT DO UPDATE`: токены/стоимость прибавляются, `requests+1`), `_schedule_usage_upsert()` (asyncio.create_task + отдельная SessionLocal), `wait_for_pending_usage_upserts()`; `record_token_usage(tokens_in, tokens_out, cost_usd=0.0)` — Redis-инкремент + durable-upsert (fail-open) |
| `app/api/v1/users.py` | `/v1/users/me/usage`: Redis-путь без изменений, плюс DB-fallback `usage_daily`; при обоих источниках `used = max(redis, db)`; ошибки БД/Redis не ломают ответ; схема ответа прежняя |
| `app/llm/openai_client.py` | Передача фактического `cost_usd` в `record_token_usage` для `complete` и `stream_generate` |
| `scripts/aggregate_usage.py` *(новый)* | CLI: `--month YYYY-MM` (default текущий UTC), `--json`, `--retention-days N` (default 90), `--no-cleanup`; месячные суммы (totals, by_subject_type, by_day), retention-удаление; exit 0/2 |
| `tests/unit/test_finops_durable.py` *(новый)* | 20 тестов: parse_subject/utc_date, no-op без субъекта, Redis-трекер, durable-upsert при сбое Redis, fail-open при сбое БД, invalid subject, month_range/parse_month/argparse, UsageTotals |
| `tests/integration/test_usage_daily.py` *(новый)* | 5 тестов: `/v1/chat` (реальный LLM-клиент + fake HTTP) → строка `usage_daily` (токены 11/7, cost, requests=1); удаление Redis-ключа → `/usage` из БД; `ON CONFLICT` накопление; `aggregate_usage.py --json` + retention; форма таблицы |

### KI-046: полный охват `llm_calls`

| Файл | Что изменилось |
| :--- | :--- |
| `app/llm/warmup.py` | `warmup_local_llm` оборачивает `generate` в `with llm_call_context(operation="warmup")` |
| `app/llm/calls.py` | `record_cache_hit(latency_ms=…)` — обёртка над `record_llm_call` (`cache_hit=True`, `provider="cache"`, `model="semantic-cache"`, tokens/cost=0); `attach_commentary_id(commentary_id)` — best-effort UPDATE `llm_calls.commentary_id` по текущему `trace_id`/последнему `span_id` в фоновой задаче (входит в общий pending-set); contextvar последнего вызова |
| `app/commentary/service.py`, `app/commentary/streaming.py` | Замер времени `lookup`; при cache-hit — `record_cache_hit`; после commit комментария — `attach_commentary_id(row.id)` (sync и SSE) |
| `app/chat/service.py`, `app/chat/streaming.py` | Аналогично для чата: cache-hit → `record_cache_hit`, замер latency lookup |
| `app/api/v1/commentary.py` | `attach_commentary_id(row.id)` после commit (sync-эндпоинт) |
| `tests/unit/test_llm_calls.py`, `tests/unit/test_warmup.py`, `tests/integration/test_llm_calls_api.py` | +warmup-row, cache-hit rows (commentary/chat), `commentary_id` для sync и SSE; 14/4/11 тестов |

### KI-047: `X-Trace-Id` на необработанных 500

| Файл | Что изменилось |
| :--- | :--- |
| `app/main.py` | `_unhandled_error_handler`: 500 `{"detail": "Internal Server Error", "code": "internal_error"}` + `X-Trace-Id` из `request.state.trace_id` (fallback `current_trace_id()`); structlog `unhandled_exception` (path/method/trace_id/тип, без текста); регистрация на `Exception` рядом с `AppError` |
| `tests/integration/test_tracing.py` | +`test_unhandled_500_has_trace_id_header` (traceparent → тот же `X-Trace-Id`, тело 500); 23 теста |

### T-034b: JSONL-экспорт в Admin UI

| Файл | Что изменилось |
| :--- | :--- |
| `app/api/v1/admin_exports.py` *(новый)* | `GET /v1/admin/exports/edits.jsonl`: NDJSON-стрим keyset-батчами (без роста памяти), `Content-Disposition: attachment; filename="edits.jsonl"`, `Cache-Control: no-store`; авторизация JWT admin/auditor (user → 403, без кредов → 401) **или** cookie `admin_session` (decode через `app.admin.decode_admin_session`); фильтры `date_from/date_to` (по `edits.created_at`, включительно), `level`, `human_edited`, `schema_version` (1|2, default 2); v2 — `build_fact_sheet`+`record_from_edit` из `scripts/export_dataset`, проблемные записи пропускаются; v1 — legacy original→edited |
| `app/admin.py` | `DatasetExportView(CustomView)` — страница `/admin/exports/dataset`, меню «Экспорт датасета», форма фильтров + кнопка на endpoint; `ADMIN_SESSION_COOKIE`, `decode_admin_session` (TimestampSigner/b64/json); регистрация view в `create_admin` |
| `app/templates/dataset_export.html` *(новый)* | Форма (даты/уровень/human_edited/schema_version) + JS-сборка query и скачивание; «Скачать без фильтров» |
| `app/main.py` | Подключение `admin_exports.router` под `api_prefix` |
| `tests/integration/test_admin_exports.py` *(новый)* | 11 тестов: 401/403/auditor/admin, cookie-сессия после `/admin/login`, v2 (пакет фактов, metadata, `fact_sheet_chars`), v1, фильтры date/level/human_edited, пустой результат 200, invalid date 422, страница admin/auditor 200 |
| `app/dataset/export.py`, `app/dataset/__init__.py` *(новые)* | **KI-051 (найдено верификатором):** переиспользуемые builder'ы вынесены из `scripts/export_dataset.py` в `app.dataset.export` (`SCHEMA_VERSION`/`LEGACY_SCHEMA_VERSION`, `ExportStats`, `build_fact_sheet`, `record_from_edit`, `load_system_prompt`); `admin_exports` больше не импортирует `scripts` (production wheel содержит только `app`) |
| `scripts/export_dataset.py` | Реэкспорт builder'ов из `app.dataset.export` (публичный контракт CLI/тестов сохранён); CLI-логика не менялась |
| `.github/workflows/ci.yml` | Docker-job: smoke `docker run --rm chessannotator:ci python -c "import app.main"` — ловит регресс «wheel без scripts» |
| `tests/test_models.py` | `usage_daily` в SPEC_TABLES + проверка UNIQUE/индексов (10 тестов) |

### Документация

`README.md` (итерация 28, 641/42, 14 таблиц/head `e9f0a1b2c3d4`,
FinOps/Observability/API, KI 51/25/26), `docs/KNOWN_ISSUES.md`
(KI-046/047/051 → Closed, KI-048 обновлён, +KI-049/050),
`TODO.md` (T-031b/T-034b → `[x]`), `docs/CHANGELOG.md`,
`docs/development/RELEASES.md`, этот отчёт.

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Durable FinOps (unit) | `tests/unit/test_finops_durable.py` | 20 |
| Durable FinOps (integration) | `tests/integration/test_usage_daily.py` | 5 |
| `llm_calls` recorder/context | `tests/unit/test_llm_calls.py` | 14 |
| Warmup | `tests/unit/test_warmup.py` | 4 |
| Интеграция `llm_calls`/cache-hit/commentary_id | `tests/integration/test_llm_calls_api.py` | 11 |
| TraceMiddleware + 500 | `tests/integration/test_tracing.py` | 23 |
| Админ-экспорт | `tests/integration/test_admin_exports.py` | 11 |
| Схема БД | `tests/test_models.py` | 10 |

Проверяемые сценарии:

- **T-031b:** `parse_subject`/`utc_date`; запись без субъекта — no-op;
  durable-upsert пишется даже при недоступном Redis; сбой БД в фоне не
  ломает запрос; `/v1/chat` (fake LLM HTTP) → строка `usage_daily` с
  токенами/стоимостью/requests=1; удаление Redis-ключа → `/usage` отдаёт
  durable-значение; `ON CONFLICT` прибавляет дельты и `requests`;
  `aggregate_usage.py --json` считает месяц и чистит старые строки.
- **KI-046:** warmup → строка `operation=warmup, status=ok`; cache-hit
  commentary/chat → `cache_hit=true`, `tokens/cost=0`, operation/prompt
  заполнены; `commentary_id` появляется после commit для sync и SSE
  (включая cache-hit путь); повторные вызовы в одном trace не ломают
  `UNIQUE(trace_id, span_id)`.
- **KI-047:** необработанный `RuntimeError` → 500 с `X-Trace-Id`,
  совпадающим с входящим `traceparent`; тело без внутренних деталей.
- **T-034b:** NDJSON + `Content-Disposition`; v2 `user` содержит
  `# Партия`/факты, `assistant` = `edited_text`, `metadata.game_id`/
  `schema_version=2`/`fact_sheet_chars`; v1 `user=original_text`;
  фильтры date_from/date_to/level/human_edited; пустой результат 200;
  invalid date 422; JWT user 403; без кредов 401; auditor 200; cookie
  `admin_session` после логина → 200; страница экспорта 200.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**641/643 passed** (backend, 1 skipped, 1 xfailed), **42/42** frontend,
**10/10** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД) | `pytest -q` (POSTGRES_PORT=5435, `chessannotator_iter28`, Redis 0, STOCKFISH_PATH, web/dist) | **641 passed, 1 skipped, 1 xfailed, 4 warnings in 315.15s**, exit 0; collected 643. Skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012; **+49** к итерации 27 (592) |
| FinOps durable unit | `pytest tests/unit/test_finops_durable.py -q` | **20 passed** |
| FinOps durable integration | `pytest tests/integration/test_usage_daily.py -q` | **5 passed** |
| LLMOps/`llm_calls` | `pytest tests/unit/test_llm_calls.py tests/unit/test_warmup.py tests/integration/test_llm_calls_api.py -q` | **29 passed** (14/4/11) |
| Admin export | `pytest tests/integration/test_admin_exports.py -q` | **11 passed** |
| Tracing/500 | `pytest tests/integration/test_tracing.py -q` | **23 passed** |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 74 source files** (exit 0) |
| Production import (KI-051) | копия только `app/` во временный каталог (эмуляция wheel без `scripts/`), `python -c "import app.main"` | **OK, exit 0** (`ModuleNotFoundError: scripts` до фикса; см. §5) |
| Alembic | `alembic upgrade head`, `alembic check` (iter28) | head `e9f0a1b2c3d4`; «No new upgrade operations detected»; цикл downgrade→upgrade проверен |
| Frontend tests | `pnpm --dir web test` | **42 passed** (6 файлов) |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 6.92 с; JS 464.31 КБ (gzip 146.15), CSS 14.29 КБ (gzip 3.77) |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed** (5 сценариев × chromium/mobile) |

---

## 4. Smoke (ручной сценарий)

**4.1. Durable FinOps (живой uvicorn :8017, real LM Studio, Redis 0,
`TOKEN_BUDGET_ENABLED=true`).**

```
POST /auth/register|login → user id=212
POST /v1/analyze (PGN 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6, traceparent) → analysis_id=120
POST /v1/chat (Bearer, тот же traceparent) → 200, tokens_used=862, wall 6.86 с
  X-Trace-Id: 5bf92f35-77b3-4da6-a3ce-929d0e0e4736  (== traceparent)

usage_daily: user | 212 | 2026-10-29 | tokens_in=818 | tokens_out=44 | cost=0 | requests=1
llm_calls:  5bf92f35-...  | chat | cache_hit=f | 818/44 | ok

DEL usage:2026-10-29:user:212  (имитация потери Redis)
GET /v1/users/me/usage → {"tier":"free","used_tokens":862,"limit_tokens":50000,
                          "remaining_tokens":49138,"enabled":true}
```

`python scripts/aggregate_usage.py --month 2026-10 --json --no-cleanup`:
`totals tokens_in=2442, tokens_out=588, requests=2`; by_subject_type —
`user` (2 субъекта), by_day — `2026-10-29`.

**4.2. KI-046 (live):** `warmup_local_llm()` → `llm_calls`:
`operation=warmup`, status=ok, model `qwen/qwen3-4b-2507`, tokens 16/4,
latency 782 мс, trace_id сгенерирован recorder'ом (HTTP-контекста нет).

**4.3. T-034b (живой сервер, seed: commentary id=90 + PATCH → edit id=70).**

| Запрос | Результат |
| :--- | :--- |
| `GET /v1/admin/exports/edits.jsonl?level=club&schema_version=2` (JWT admin) | 200, 1 строка NDJSON, `Content-Type: application/x-ndjson`, `Content-Disposition: attachment; filename="edits.jsonl"`, `X-Trace-Id` |
| то же (JWT auditor) | 200 |
| то же (JWT user) | 403 `forbidden` |
| без кредов | 401 |
| `schema_version=1` | 200, `schema_version=1`, `user` = `original_text` |
| cookie: GET `/admin/login` → CSRF → POST login (303) → GET export с cookie | 200 |
| `GET /admin/exports/dataset` (admin/auditor) | 200 (страница с формой) |

**4.4. KI-047:** проверено интеграционным тестом (необработанный
`RuntimeError` → 500 + `X-Trace-Id` == traceparent); в живом сервере
специально не провоцировался.

**4.5. Миграция.** `alembic current` = `e9f0a1b2c3d4` (head);
`downgrade d8e9f0a1b2c3` → `upgrade head` успешно; `alembic check` —
«No new upgrade operations detected». Схема `usage_daily` сверена с
DESIGN §4.5: UNIQUE(три поля), два индекса, CHECK ≥ 0.

---

## 5. Что не так

1. **Durable-upsert — fire-and-forget (KI-049).** Если процесс упадёт
   между Redis-`INCRBY` и INSERT/COMMIT, дельта не попадёт в
   `usage_daily`; после рестарта Redis-ключ теряется. `/usage` использует
   `max(redis, db)`, поэтому показатель не «откатывается вниз», но
   последняя дельта может потеряться.
2. **Месячной агрегации нет планировщика (KI-049).** Скрипт
   `scripts/aggregate_usage.py` работает (JSON/retention), но cron/ARQ/
   Temporal не настроены; алертов 80/100% и reserve/reconcile нет.
3. **v2-экспорт может быть медленным (KI-050).** На каждую правку
   строится пакет фактов (`load_analyses` + `build_fact_package`);
   память не растёт (keyset-батчи), но на десятках тысяч строк выгрузка
   займёт заметное время. Фоновой задачи «>5 с»/подписанной ссылки нет.
4. **Cookie-сессия админки разбирается вручную (KI-050).**
   `decode_admin_session` повторяет internals `SessionMiddleware`
   (TimestampSigner/b64/json); при мажорном апдейте Starlette формат
   может измениться — покрыто тестами, но это связанность.
5. **Аудит выгрузки не пишется (KI-050).** `audit_logs` для
   `dataset.export` не добавлен; checksum/размер строки — тоже.
6. **KI-051 найден и закрыт в итерации (production import).** Верификатор
   обнаружил, что `app/api/v1/admin_exports.py` импортировал
   `scripts.export_dataset`, а production wheel/образ содержат только
   `app` → `uvicorn app.main:app` падал с `ModuleNotFoundError: scripts`.
   Исправлено вынесением builder'ов в `app.dataset.export` (скрипт
   реэкспортирует их) и smoke-шагом в CI
   (`docker run … python -c "import app.main"`); проверено эмуляцией
   wheel-каталога без `scripts/` — импорт успешен.
7. **Warnings полного прогона (4).** Включая
   `RuntimeWarning: coroutine 'Connection._cancel' was never awaited`
   (фоновые сессии/GC asyncpg в тестах) и
   `StarletteDeprecationWarning` про `httpx`/`TestClient` (KI-005);
   на результат не влияют, но шумят.
8. **KI-048 остаётся Open.** `llm_calls` по-прежнему без
   партиционирования/retention; `usage_daily` теперь есть, но 90-дневное
   удержание для `llm_calls` — ручное.
9. **Тестовый прогон требует Stockfish/PostgreSQL.** Интеграционные
   тесты итерации скипаются без БД/движка (как и раньше); на CI сервисы
   подняты.
10. **KI-030 не закрыт.** LoRA на GPU не обучалась; датасет v2 и
    `llm_calls`/`usage_daily` теперь дают данные для оценки.
11. **Design drift `usage_daily` vs DESIGN §4.5.** Фактическая схема
    отличается от первоначального наброска DESIGN: `subject_type`
    varchar(8) вместо enum `budget_subject`, `subject_key` varchar(64)
    вместо TEXT, `requests` INTEGER вместо BIGINT, surrogate `id` +
    UNIQUE(subject_type, subject_key, day) вместо composite PK, нет
    `global`-субъекта. Это осознанно: MVP T-031b закрывает durable-учёт
    по постановке (subject_type/subject_key/day/tokens/cost/requests);
    enum/глобальные лимиты — при реализации месячных бюджетов
    (KI-049/T-031c). Миграция и отчёт описывают фактическую схему.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| **KI-049** | Low | finops, db | Durable-upsert `usage_daily` fire-and-forget: потеря последней дельты при жёстком падении; месячная агрегация — ручной скрипт без планировщика/алертов |
| **KI-050** | Low | admin, api | v2-экспорт медленный на больших объёмах (пакет фактов на строку); cookie-сессия админки разбирается вручную; нет аудита факта выгрузки/checksum/фоновой ссылки |
| **KI-051** | Med | packaging, ci | Production wheel/образ не содержал `scripts/`, а `app` импортировал `scripts.export_dataset` → `uvicorn app.main:app` падал. **Закрыт в итерации 28** (builder'ы → `app.dataset.export`, CI smoke-import) |

**Закрыты:** KI-046 (warmup/commentary_id/cache_hit), KI-047
(`X-Trace-Id` на необработанных 500), KI-051 (production import).
**KI-048** остаётся **Open** (retention/партиционирование `llm_calls`).
Сводка реестра: **51** запись, 25 Open, 26 Closed.

---

## 7. Обоснование

- **Почему durable-upsert в `record_token_usage`, а не в middleware.**
  Токены и стоимость известны только после ответа LLM; middleware видит
  лишь флаг «запрос прошёл». Запись из `record_token_usage` имеет точные
  токены/стоимость, уже вычисленный субъект (contextvar) и может быть
  полностью фоновой — горячий путь не ждёт PostgreSQL. Middleware
  остаётся быстрой проверкой 429 по Redis.
- **Почему `ON CONFLICT DO UPDATE`, а не SELECT+UPDATE.** Один атомарный
  запрос исключает гонку двух параллельных LLM-вызовов одного
  пользователя и лишний round-trip; `requests` инкрементируется там же.
  Отдельная сессия в фоне, чтобы не мешать транзакции эндпоинта.
- **Почему `/usage` = `max(redis, db)`.** Redis — оперативный счётчик
  (может быть сброшен/потерян), БД — durable-история (может отставать на
  время фоновой записи). Максимум показывает лучшую известную оценку и
  не даёт показателю «упасть» после рестарта Redis; при недоступной БД
  остаётся Redis-путь, при пустом Redis — durable.
- **Почему отдельный `scripts/aggregate_usage.py`, а не эндпоинт.**
  Месячные отчёты и retention — административная операция, её не нужно
  отдавать в публичный API; скрипт переиспользует те же модели/настройки
  и запускается cron'ом. `--retention-days` сразу решает задачу
  удержания durable-агрегата без отдельной миграции.
- **Почему warmup через `llm_call_context`, а не отдельный INSERT.**
  Recorder уже умеет писать все поля и генерировать trace, если HTTP-
  контекста нет; обёртка в контекст — минимальная правка, и warmup
  попадает в ту же admin-view/аналитику, что и реальные вызовы (включая
  статус ok/error).
- **Почему `commentary_id` обновляется после commit, а не буферизуется.**
  Буферизация (DESIGN §4.4) потребовала бы держать запись в памяти до
  INSERT комментария и усложнила бы streaming/ошибки. Best-effort UPDATE
  по `trace_id` (и последнему `span_id`) проще, не блокирует ответ и
  идемпотентен; при потере UPDATE поле останется NULL — это допустимо
  для аналитики, но не ломает контракт.
- **Почему cache-hit пишется в `llm_calls`.** Это единственный способ
  видеть в одной таблице и реальные вызовы, и сэкономленные (0 токенов)
  ответы: `cache_hit=true` позволяет считать hit rate и экономию
  SQL-агрегатом, без отдельной метрики.
- **Почему handler на `Exception`, а не middleware.** `ServerErrorMiddleware`
  стоит снаружи пользовательских middleware и сам формирует 500; app-level
  handler для `Exception` — штатная точка Starlette/FastAPI, где ещё
  доступен `request.state.trace_id`, выставленный TraceMiddleware до
  входа в приложение. Ответ не раскрывает внутренние детали, только
  trace_id для поддержки.
- **Почему экспорт NDJSON + keyset-батчи.** Датасет LoRA — поток строк,
  JSONL читается тренировочным скриптом; keyset по `edits.id` не требует
  OFFSET и держит память константной. v2 переиспользует существующий
  `scripts/export_dataset`, чтобы админка и CLI давали одинаковый формат.
- **Почему cookie-сессия для кнопки.** Браузер в админке не имеет
  Bearer-токена (access хранится в памяти фронтенда), а
  `admin_session` уже стоит после парольного логина; endpoint читает ту
  же подпись, что `SessionMiddleware`, поэтому кнопка «Скачать» работает
  без ручного копирования JWT. JWT-путь остаётся для скриптов/CI.
- **Почему фильтр `human_edited` по `commentaries`, а не по `edits`.**
  Правка в `edits` уже означает действие человека; флаг
  `commentaries.human_edited` различает комментарии, отредактированные
  через PATCH (он выставляется при правке), и служебные/импортированные
  записи. Фильтр по нему соответствует постановке T-034b.
- **Почему нет фоновой выгрузки/аудита сразу.** DoD итерации — рабочий
  UI-экспорт с фильтрами и ролевой защитой; большие выгрузки, checksum,
  подписанные ссылки и `audit_logs` вынесены в KI-050, чтобы не
  раздувать итерацию (аудит-схема и фоновая очередь — отдельные решения).
- **Почему export-builder'ы переехали в `app.dataset`.** API-слой не должен
  зависеть от CLI-скриптов: production wheel (`packages = ["app"]`) и
  Docker-образ не содержат `scripts/`, поэтому импорт из `app` в `scripts`
  роняет `uvicorn` (KI-051). Вынос в `app.dataset.export` убирает
  антипаттерн, сохраняет единый источник формата для CLI и API, а
  CI-smoke `python -c "import app.main"` в собранном образе не даёт
  регрессу вернуться незамеченным.
