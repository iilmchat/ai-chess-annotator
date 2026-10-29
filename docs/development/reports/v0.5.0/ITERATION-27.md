# Итерация 27 — LLMOps Observability и Security Fixes (T-030, T-030b, KI-043, KI-045)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-043 и KI-045 закрыты**; **T-030/T-030b
закрыты в объёме MVP** (дашборды — T-109). Заведены KI-046…048.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### LLMOps: `llm_calls` и trace (T-030)

| Файл | Что изменилось |
| :--- | :--- |
| `alembic/versions/d8e9f0a1b2c3_add_llm_calls.py` *(новый)* | Миграция `llm_calls` (down_revision `c7d8e9f0a1b2`, head): 21 колонка по DESIGN §4.4, FK `users/games/commentaries` ON DELETE SET NULL, `UNIQUE(trace_id, span_id)`, индексы `BRIN(created_at)`, `(model, created_at DESC)`, `(level, created_at DESC)`, CHECK на неотрицательность токенов/стоимости/латентности; upgrade+downgrade |
| `app/db/models.py` | Модель `LlmCall` (UUID trace_id, enum `level` nullable, `Numeric(12,6)` cost_usd, prompt_slug/version, статусы, checks) |
| `app/tracing.py` *(новый)* | W3C `traceparent` (regex `00-32hex-16hex-flags`, нулевые id невалидны), `TraceContext`, contextvars (`current_trace_id/span_id/request_id`), `trace_id_for_span` (UUID из contextvars или hex OTel — общий helper для validator/RAG), `TraceMiddleware` (state, `X-Trace-Id` в ответе, `X-Request-Id`, structlog-binding), `request_span` (OTel `http_request`, trace id == W3C) |
| `app/llm/calls.py` *(новый)* | `LlmCallContext`, `llm_call_context`, `stream_in_call_context`, `record_llm_call` (fire-and-forget `asyncio.create_task` + отдельная сессия), `_persist_call` (best-effort), `wait_for_pending_llm_calls`, `_prompt_version` (select `prompts.version`), без PII |
| `app/llm/openai_client.py` | Запись в `finally` для `complete` и `stream_generate`: provider/model/tokens/cost/latency/status (ok|error|timeout|invalid), error_code, отмена/закрытие стрима; `cache_hit=False` |
| `app/main.py` | `TraceMiddleware` регистрируется самым внешним (заголовок и на 404/429/SSE) |
| `app/api/v1/commentary.py`, `app/api/v1/chat.py` | Контекст `llm_call_context(operation=..., actor_id=user.id, game_id=analysis_id, prompt_slug=level)`; для SSE — `stream_in_call_context`; публичные контракты не менялись |
| `app/observability.py` | `get_tracer(name)` — отдельные трейсеры для HTTP/LLM/validator/RAG; при `OTEL_ENABLED=false` — no-op |
| `tests/unit/test_llm_calls.py` *(новый)* | 10 тестов recorder/context/фоновой записи/prompt_version/ошибок |
| `tests/integration/test_llm_calls_api.py` *(новый)* | 5 тестов: TestClient + mock LLM → row в `llm_calls`, sync/stream, X-Trace-Id, traceparent |
| `tests/integration/test_tracing.py` *(новый)* | 22 теста TraceMiddleware: парсинг/генерация traceparent, X-Trace-Id, X-Request-Id, поведение на 4xx/SSE |
| `tests/unit/test_llm.py`, `tests/unit/test_observability.py` | Дополнены записями/спанами; OTel-вложенность `http_request`→`llm_*` проверяется здесь (`test_request_span_has_trace_id_from_w3c_context`, `test_llm_span_is_nested_under_request_span`) |

### OTel-спаны validator/RAG (T-030b)

`app/engine/validator.py` — span `validator.validate` в `validate_moves`
(attrs moves/valid/illegal_count/mainline_moves/extra_fens, Status OK/ERROR),
тело вынесено в `_validate_moves_impl`, сигнатуры не изменены;
`app/rag/service.py` — span `rag.retrieve` в `find_similar_positions`
(top_k/results, OK/ERROR); `trace.id` из `app.tracing.current_trace_id()`
(ленивый импорт) с fallback на активный OTel-span;
`tests/unit/test_observability_validator_rag.py` *(новый, 8 тестов)*.

### Security fixes

| Файл | Что изменилось |
| :--- | :--- |
| `app/auth/passwords.py` | `DUMMY_PASSWORD_HASH` — Argon2id-литерал с параметрами `PasswordHasher()` по умолчанию |
| `app/api/v1/auth.py` | KI-043: при `user is None` — `asyncio.to_thread(verify_password, payload.password, DUMMY_PASSWORD_HASH)` и тот же 401; для существующего — `verify_and_update` |
| `tests/unit/test_auth.py` | dummy-хеш: тип/параметры, dummy не подходит, monkeypatch вызова с DUMMY_HASH, существующий email — stored-хеш |
| `tests/integration/test_auth_api.py` | Интеграционный тайминг 7+7 (прогрев, медианы, порог ratio ≤2, обе >5 мс) |
| `app/admin.py` | KI-045: guard выставляет `request.state.admin_role`/`admin_actor`, блокирует любые небезопасные методы auditor'а → 403 JSON; role-aware hooks (`PromptAdmin.can_edit`, `can_import`, `can_export`), `LlmCallAdmin` (read-only) + регистрация |
| `tests/integration/test_admin.py` | 30 тестов: auditor read-only (фактические статусы всех mutation-маршрутов, БД не меняется), admin edit 200/303, user 403, legacy-пути, llm-call view |

### Документация

`README.md` (итерация 27, тесты 592/42, таблицы 13/head `d8e9f0a1b2c3`,
Observability, KI 48/25/23), `docs/KNOWN_ISSUES.md` (KI-043/KI-045 →
Closed, +KI-046…048), `TODO.md` (T-030/T-030b → `[x]`, T-028/T-043
уточнения), `docs/CHANGELOG.md`, `docs/development/RELEASES.md`, этот
отчёт.

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Recorder/контекст/фоновая запись | `tests/unit/test_llm_calls.py` | 10 |
| LLM-клиент/спаны + OTel-вложенность | `tests/unit/test_llm.py`, `test_observability.py` | дополнены |
| Интеграция записи вызовов | `tests/integration/test_llm_calls_api.py` | 5 |
| TraceMiddleware/W3C | `tests/integration/test_tracing.py` | 22 |
| Спаны validator/RAG | `tests/unit/test_observability_validator_rag.py` | 8 |
| Timing-safe login | `tests/unit/test_auth.py`, `tests/integration/test_auth_api.py` | дополнены (всего 43/26) |
| RBAC admin + llm_calls view | `tests/integration/test_admin.py` | 30 |
| Схема БД (`llm_calls`) | `tests/test_models.py` | +1 (всего 9) |
| E2E | `web/e2e/*.spec.ts` | 5 сценариев × 2 проекта |

Проверяемые сценарии:

- **`llm_calls`:** запись после `complete` и `stream_generate` (включая
  ошибку/таймаут/отмену), статусы ok|error|timeout|invalid, свой
  `span_id` на каждый вызов (UNIQUE(trace, span) не отбрасывает retry),
  `prompt_slug=level`, `prompt_version` из `prompts.version`, пустой
  контекст — no-op, ошибка INSERT не ломает запрос, PII не пишется.
- **trace:** валидный `traceparent` → trace id в `X-Trace-Id` и
  `request.state`; битый/нулевой → новый UUID; `X-Request-Id` попадает в
  `llm_calls.request_id`; structlog-контекст; OTel-span `http_request`
  с тем же trace id и вложенными `llm_*`; при OTEL_ENABLED=false —
  no-op, но `X-Trace-Id`/запись работают.
- **validator/RAG:** имена спанов, атрибуты без PII, Status OK/ERROR,
  `trace.id`; старые тесты зелёные.
- **KI-043:** для несуществующего email dummy-Argon2 выполняется (unit),
  медианы времени отличаются ≤2x (интеграция), dummy не используется для
  существующего.
- **KI-045:** auditor — `GET /admin` 200 и все list-страницы 200, но
  `GET/POST edit|delete|action|import|inline-edit` → 403/405 без изменения
  БД; admin — edit 200/303+сохранение; user — 403; legacy X-Admin-Token и
  парольная сессия — как admin; llm-call view read-only для обоих.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**592/594 passed** (backend, 1 skipped, 1 xfailed), **42/42** frontend,
**10/10** E2E mocked.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД) | `pytest -q` (POSTGRES_PORT=5435, `chessannotator_iter27`, Redis 0, STOCKFISH_PATH, web/dist собран) | **592 passed, 1 skipped, 1 xfailed, 1 warning in 279.32s**, exit 0; collected 594. Skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012; **+70** к итерации 26 (522) |
| LLMOps набор | `pytest tests/unit/test_llm_calls.py tests/unit/test_llm.py tests/unit/test_observability.py tests/integration/test_llm_calls_api.py tests/integration/test_tracing.py -q` | **65 passed** (включая 5 интеграционных) |
| Validator/RAG-спаны | `pytest tests/unit/test_observability_validator_rag.py tests/unit/test_validator.py tests/unit/test_rag.py -q` | passed (8 новых) |
| Auth timing | `pytest tests/unit/test_auth.py tests/integration/test_auth_api.py -q` | **69 passed**; медианы 55.4/54.8 мс, ratio 1.01 |
| Admin RBAC | `pytest tests/integration/test_admin.py -q` | **30 passed** |
| Модель/схема | `pytest tests/test_models.py -q` | **9 passed** (llm_calls: уникальность/индексы/обязательные поля) |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 71 source files** (exit 0) |
| Alembic | `alembic upgrade head`, `alembic check` (iter27) | head `d8e9f0a1b2c3`; «No new upgrade operations detected»; цикл downgrade→upgrade проверен на `iter27_llmops` |
| Frontend tests | `pnpm --dir web test` | **42 passed** (6 файлов) |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 4.23 с; JS 464.31 КБ (gzip 146.15), CSS 14.29 КБ (gzip 3.77) |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed** (5 сценариев × chromium/mobile) |

---

## 4. Smoke (ручной сценарий)

**4.1. Живой uvicorn :8014 (`APP_ENV=dev`, `chessannotator_iter27`).**

```http
GET /v1/nope → 404, x-trace-id: c21abf5f-1e1e-4d0e-b359-22481ccb4df0
POST /auth/login → 200 {access_token, token_type, expires_in, user}
POST /v1/analyze с header traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
  → analysis_id=86, moves=6
POST /v1/chat (Bearer, traceparent тот же, реальная LM Studio)
  → 200, reply_len=119, tokens_used=848, wall 5.3 с
  → X-Trace-Id: 4bf92f35-77b3-4da6-a3ce-929d0e0e4736  (== traceparent)
```

Строка в БД `llm_calls` (реальная LM Studio):
```
trace_id=4bf92f35-77b3-4da6-a3ce-929d0e0e4736
span_id=e9c87d5ff5f9505d  operation=chat  provider=lmstudio
model=qwen/qwen3-4b-2507  level=club  prompt_slug=club  prompt_version=1
tokens_in=819  tokens_out=29  latency_ms=5176  status=ok
actor_id=147  game_id=86
```
Дополнительно в БД появились строки от mock-интеграционных тестов
(`operation=commentary`, sync+stream) — recorder пишет и при стриме.

**4.2. Timing-safe login (живой сервер, curl).** После прогрева
(существующий/несуществующий email) замеры, мс: существующий
`62.0, 66.6, 65.8, 65.2, 67.3`; несуществующий
`56.9, 57.7, 58.9, 57.6, 57.2` — Argon2 выполняется в обеих ветках
(разница ~1.1x, а не «мгновенно vs 55 мс»). Точные медианы интеграционного
теста: **55.4 / 54.8 мс, ratio 1.01**.

**4.3. RBAC admin (живой uvicorn, JWT admin/auditor/user).**

| Креды | `/admin` | `/admin/llm-call/list` | `GET /admin/prompt/edit` | `POST /admin/prompt/delete` |
| :--- | ---: | ---: | ---: | ---: |
| admin | 200 | 200 | 200 | 403 (read-only view) |
| auditor | 200 | 200 | **403** | **403** |
| user | 403 | 403 | 403 | 403 |
| без кредов | 401 | 401 | 401 | 401 |

POST не изменил промпт; auditor не может ни редактировать, ни удалять,
ни импортировать (guard + hooks).

**4.4. Миграция.** `alembic current` = `d8e9f0a1b2c3` (head);
`downgrade c7d8e9f0a1b2` → `upgrade head` успешно;
`alembic check` → «No new upgrade operations detected». Схема сверена
DESIGN §4.4: 21 колонка, FK SET NULL, UNIQUE(trace_id, span_id), BRIN и
DESC-индексы.

**4.5. Устойчивость recorder.** Unit-тест: при исключении INSERT (упавшая
БД) ответ API успешен, ошибка только в логе (`llm_call_record_failed`);
вне trace-контекста/`llm_call_context` записей нет (скрипты/юнит-тесты не
ходят в БД).

---

## 5. Что не так

1. **Неполный охват `llm_calls` (KI-046).** Warmup локальной модели при
   старте не оборачивается в `llm_call_context(operation="warmup")` —
   в таблицу не попадает; `commentary_id` всегда NULL (комментарий
   INSERT-ится после генерации, буферизация телеметрии из DESIGN §4.4 не
   реализована); `cache_hit` не выставляется для semantic-cache-ответов
   (кэш отдаёт текст без вызова LLM-клиента).
2. **`X-Trace-Id` на необработанных 500 (KI-047).** `ServerErrorMiddleware`
   Starlette стоит снаружи пользовательских middleware, поэтому ответ
   необработанного исключения может прийти без заголовка (обработанные
   ошибки 4xx/5xx заголовок имеют; проверено на 404). `trace_id` при этом
   есть в structlog.
3. **Retention/партиционирование `llm_calls` (KI-048).** MVP: обычная
   append-only таблица с BRIN; 90-дневное удержание, партиции по месяцу и
   durable-агрегаты `usage_daily` (DESIGN §4.4) не сделаны.
4. **Формат `trace.id` в спанах неоднороден.** При включённом
   TraceMiddleware (HTTP) — UUID с дефисами; при прямом вызове сервиса без
   middleware (скрипты/тесты) fallback — hex OTel trace id. Для корреляции
   внутри HTTP это одно значение; вне HTTP формат может отличаться.
5. **`level` в спанах validator/RAG не выставляется** — его нет в
   сигнатурах этих сервисов; `llm.level` в LLM-спанах есть.
6. **auditor не может экспортировать.** В starlette-admin экспорт —
   POST-действие, а guard блокирует auditor'у все небезопасные методы.
   В ТЗ «read-only» — сознательный fail-closed; если аудитору нужен
   экспорт как read-операция, это точечное исключение в guard.
7. **Legacy `X-Admin-Token` — полный write-доступ.** Оставлен как
   service-token; рекомендация — деприкейт после перевода автоматизации
   на JWT (зафиксировано в KI-045-Closed-примечании).
8. **Docker/Postgres-инцидент.** В середине итерации Docker VM упал
   (`SIGSEGV`/overlayfs I/O error, `snapshotter.Usage failed`), БД 5435
   перестала отвечать; Lead восстановил через `docker desktop restart`,
   контейнеры и данные (включая новые миграции) — целы, работа
   продолжена без потерь. Инцидент — среды, не кода; в отчёте фиксируется
   для истории.
9. **`pytest` skip SPA-тестов.** При первом полном прогоне `web/dist`
   отсутствовал (удалён в конце итерации 26), поэтому `test_spa.py`/
   `test_web_static.py` скипались; после `pnpm --dir web build` финальный
   прогон — 592 passed, 1 skipped (только `DEEPSEEK_API_KEY`).
10. **Дашбордов p50/p95/error rate/cost нет** — T-030 закрыт в объёме
    спанов + durable-данных; визуализация — T-109 (Grafana/Tempo).
11. **KI-030 не закрыт.** Реальное LoRA-обучение на GPU не выполнялось;
    LLMOps-данные (`llm_calls`, trace) теперь позволяют измерять
    латентность/стоимость/ошибки будущих eval-прогонов.

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| **KI-046** | Low | observability, llm | Неполный охват `llm_calls`: warmup не обёрнут, `commentary_id` NULL, `cache_hit` semantic-cache не заполняется |
| **KI-047** | Low | observability | Необработанные 500 без `X-Trace-Id` (Starlette `ServerErrorMiddleware` вне пользовательских middleware) |
| **KI-048** | Low | db, observability | `llm_calls` без retention/партиционирования и durable-агрегатов (`usage_daily`), DESIGN §4.4 план |

**Закрыты:** KI-043 (timing-safe login, dummy-Argon2id), KI-045 (RBAC
auditor read-only в `/admin`).
**KI-030** остаётся **Open** (обучение LoRA не выполнялось — нет GPU).
Сводка реестра: **48** записей, 25 Open, 23 Closed.

---

## 7. Обоснование

- **Почему `llm_calls` отдельной таблицей, а не только OTel.** OTel-спаны
  живут в бэкенде и могут сэмплироваться; для FinOps/LLMOps-истории нужен
  durable-источник истины с SQL-фильтрами (модель/уровень/статус/период),
  не зависящий от коллектора. Схема взята из DESIGN §4.4, чтобы admin-view
  и будущие дашборды не переделывать.
- **Почему запись fire-and-forget.** LLMOps не должен влиять на
  латентность/доступность API: INSERT выполняется фоновой задачей с
  отдельной сессией, ошибки — `warning`; тесты ждут `wait_for_pending_llm_calls`.
  Альтернатива (await INSERT в запросе) добавила бы БД-операцию в горячий
  путь и риск 500 при сбое телеметрии.
- **Почему `span_id` свой на каждый вызов LLM.** В одном HTTP-запросе
  бывает несколько LLM-вызовов (retry комментария, sync+stream); если
  писать request-span id, `UNIQUE(trace_id, span_id)` отбрасывал бы
  вторую попытку. Request span id остаётся в `request.state`/логах,
  связь — по `trace_id`.
- **Почему W3C `traceparent` и `X-Trace-Id`.** W3C Trace Context —
  стандарт, который принимают OTel-коллекторы и прокси; `X-Trace-Id`
  даёт клиенту/саппорту простой идентификатор без разбора заголовков.
  Нулевые/битые id заменяются новыми (запрос не отклоняется) — трейсинг
  не должен ломать API. При `OTEL_ENABLED=false` спаны no-op, но
  `trace_id`/`llm_calls`/заголовок работают — observability и трейсинг
  развязаны.
- **Почему prompt_slug/version вычисляются в recorder.** У LLM-клиента нет
  доступа к версии промпта; сервисы знают только уровень. Recorder при
  записи делает `select(prompts.version)` по `level` (fallback NULL) —
  это дешево (один запрос в фоне) и даёт ответ на вопрос «какая версия
  промпта дала сбой» без изменения сигнатур сервисов.
- **Почему dummy-Argon2id, а не задержка-«сон».** Одинаковое время должно
  быть следствием одинаковой работы; `verify_password` на фиктивном
  хеше с теми же параметрами выравнивает и CPU-стоимость, и кэш-эффекты.
  Константа сгенерирована тем же `PasswordHasher()`; тест фиксирует, что
  dummy вызывается именно для несуществующего email.
- **Почему RBAC двумя слоями (guard + hooks).** Guard даёт единый
  403 JSON на любые mutation-методы auditor'а (включая маршруты
  starlette-admin, которые не видны как классические REST); role-aware
  hooks дополнительно скрывают/запрещают действия на уровне view (defense
  in depth). Fallback роли без контекста — `auditor` (fail-closed), чтобы
  ошибка wiring'а не выдала лишние права.
- **Почему `can_import=False` для всех.** Import — POST-действие, которое
  вставляло строки в БД в обход аудита; в read-only дизайне итераций 16–26
  его не должно быть. Включение импорта для admin — отдельное решение с
  аудитом.
- **Почему нет партиционирования/дашбордов сейчас.** MVP: таблица с
  BRIN/DESC-индексами закрывает DoD (записи, trace, view) и не требует
  управления партициями/крон-ротацией; retention и агрегаты вынесены в
  KI-048/T-109, чтобы не блокировать итерацию.
- **Почему уровень `level` не проброшен в validator/RAG.** Это потребовало
  бы изменения публичных сигнатур сервисов (используются в тестах/CLI);
  `llm.level` в LLM-спанах есть, а validator/RAG-спаны и так коррелируют
  по `trace_id`. Проброс — при появлении реальной потребности.
