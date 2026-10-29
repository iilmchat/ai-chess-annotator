# Итерация 29 — LLMOps Dashboard и Retention (T-109, KI-048, KI-050)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-048 закрыт** (retention `llm_calls`);
**T-109 закрыт** (LLMOps-дашборд в админке); **KI-050 частично закрыт**
(аудит выгрузки датасета). Заведён **KI-052** (партиционирование
`llm_calls` — опциональная часть постановки не делалась).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### KI-048: retention `llm_calls`

| Файл | Что изменилось |
| :--- | :--- |
| `scripts/prune_llm_calls.py` *(новый)* | CLI-retention: `PruneStats`, `resolve_cutoff` (UTC, защита от `retention_days < 1`), `count_expired` (`--dry-run`), `prune_batches` — цикл `DELETE FROM llm_calls WHERE id IN (SELECT id ... WHERE created_at < cutoff ORDER BY id LIMIT batch_size)` с commit на батч и паузой `--sleep-ms`; `--retention-days` (default из `Settings.llm_calls_retention_days` = 90), `--batch-size` (1000), `--max-batches`, `--sleep-ms`, `--dry-run`, `--json`; exit code 0/2 |
| `app/config.py` | `llm_calls_retention_days: int = 90` (Observability) |
| `.env.example` | `LLM_CALLS_RETENTION_DAYS=90` с отсылкой к cron-разделу |
| `tests/unit/test_prune_llm_calls.py` *(новый)* | 9 тестов: cutoff (UTC/naive/ошибки), argparse defaults/overrides/invalid, JSON-форма `PruneStats` |
| `tests/integration/test_prune_llm_calls.py` *(новый)* | 4 теста на живой БД: удаляются только старые строки батчами (5 old + 3 new, batch=2); `--max-batches` ограничивает и оставляет хвост; `--dry-run` считает без удаления; CLI `main([...]) --json` → exit 0 и удаление |
| `docs/development/GETTING_STARTED.md` | Новый §9 «Обслуживание: retention и FinOps»: команды, `--dry-run`, cron/systemd timer/Task Scheduler; §9.2 — `aggregate_usage.py`; прежний §9 → §10 |
| `docs/development/DESIGN_ADMIN_AND_OPS.md` | §4.4: retention реализован скриптом, партиционирование не сделано (KI-052); §7.4: реализованный дашборд `/admin/llmops` |

### T-109: LLMOps-дашборд в админке

| Файл | Что изменилось |
| :--- | :--- |
| `app/llmops_dashboard.py` *(новый)* | `PERIODS` (24h/7d/30d), dataclass-ы `LatencyStats`/`ModelUsage`/`TopUserRow`/`PeriodMetrics`/`DashboardData`; `collect_dashboard` — агрегаты по `llm_calls`: `count`, tokens/out/cost, ошибки `error`/`timeout`, `cache_hit`; latency `percentile_cont(0.5/0.95/0.99)` + `avg` **только по `cache_hit=false`**; разбивки по `model`/`level`; топ-5 из `usage_daily` по сумме токенов; `LlmOpsDashboardView(CustomView)` — `/admin/llmops` |
| `app/templates/llmops_dashboard.html` *(новый)* | Jinja2 + Bootstrap: сводная таблица (вызовы, p50/p95/p99, ошибки/error rate, cache/хит-рейт, токены, стоимость), 3 блока «по моделям и уровням», 3 карточки «Топ-5 пользователей», ссылки на журнал вызовов; без JS/React |
| `app/admin.py` | Импорт/`__all__`/`admin.add_view(LlmOpsDashboardView())` |
| `tests/unit/test_llmops_dashboard_unit.py` *(новый)* | 6 тестов: окна 24h/7d/30d, `_rate` (в т.ч. 0/0), `_group_key` (enum/NULL), `tokens_total`, `subject`, пустая latency |
| `tests/integration/test_admin_llmops_dashboard.py` *(новый)* | 7 тестов: точные агрегаты за 24h/7d/30d (requests/errors/tokens/cost/percentile/разбивки), top-5 из `usage_daily` (окна и порядок), пустые таблицы → нули; страница `/admin/llmops` — 200 для admin-token/auditor JWT, 403 для `user`, 401 без кредов, пустое состояние |
| `docs/development/ARCHITECTURE.md` | §2: строки `app/admin.py`, `app/llmops_dashboard.py`, `admin/exports` |
| `README.md` | Бейдж 671 тест, итерация 29, API/Observability/Audit/реестр KI |

### KI-050: аудит выгрузки датасета

| Файл | Что изменилось |
| :--- | :--- |
| `app/api/v1/admin_exports.py` | `_audit_dataset_export` — best-effort запись `audit_logs action="dataset.export"` в отдельной сессии (актор, date_from/date_to/level/human_edited/schema_version, `rows`, `completed`, `trace_id`); `_iter_edits_jsonl` считает фактически отданные строки и вызывает аудит в `finally` (обрыв стрима → `completed=false`); эндпоинт передаёт `_actor` |
| `tests/integration/test_admin_exports.py` | +4 теста: аудит с фильтрами и `rows=1` (JWT), пустая выгрузка (`rows=0`), актор cookie-сессии (`session:admin`), аудитор-JWT |
| `docs/KNOWN_ISSUES.md` | KI-048 → **Closed** (решение, остаток → KI-052); KI-050 — аудит реализован, остаток checksum/фоновая выгрузка; +KI-052; сводка 52/25/27 |
| `TODO.md` | T-109 → `[x]` с фактическим объёмом; актуализированы остатки T-030/T-031b/T-034b (KI-048 закрыт, партиционирование — KI-052) |
| `docs/CHANGELOG.md`, `docs/development/RELEASES.md` | Записи итерации 29 |
| `docs/development/reports/v0.6.0/ITERATION-29.md` *(новый)* | Этот отчёт |

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| Retention CLI (unit) | `tests/unit/test_prune_llm_calls.py` | 9 |
| Retention на БД (integration) | `tests/integration/test_prune_llm_calls.py` | 4 |
| Дашборд: dataclass/хелперы (unit) | `tests/unit/test_llmops_dashboard_unit.py` | 6 |
| Дашборд: SQL + страница (integration) | `tests/integration/test_admin_llmops_dashboard.py` | 7 |
| Аудит экспорта (integration) | `tests/integration/test_admin_exports.py` | 4 из 15 |

Проверяемые сценарии:

- **Retention:** граница `now − N дней` (aware/naive UTC, `N=0` → ошибка);
  старые строки удаляются, свежие остаются; удаление идёт порциями по
  `--batch-size` (5 строк / batch=2 → ≥3 коммита); `--max-batches=1`
  удаляет ровно один батч и оставляет хвост (`capped=true`);
  `--dry-run` ничего не удаляет; CLI `--json` возвращает `deleted/batches/
  cutoff` и exit 0; повторный запуск удаляет 0.
- **Дашборд:** точные значения за 24h/7d/30d на детерминированном сиде:
  requests/errors/error rate, tokens in/out, cost, cache hit rate; latency
  p50/p95/p99 и avg по 2/3/4 значениям, **cache-hit (2 мс) не попадает в
  перцентили**; разбивки по `model`/`level`; топ-5 пользователей
  (`user:42`, `user:7`, `ip:1.2.3.4`) с отсечением записи старше 30 дней;
  пустые таблицы → нули и `None` (без деления на ноль); страница 200 для
  admin-token и auditor JWT, 403 для `user`, 401 без кредов, пустое
  состояние с текстом «Нет вызовов за окно.».
- **Аудит экспорта:** после NDJSON-стрима появляется
  `audit_logs.action=dataset.export` с `actor=jwt:<email>` (или
  `session:admin` для cookie-сессии), фильтрами и `rows`; пустой
  результат аудируется с `rows=0`; `completed=true` при полном стриме.

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**671/673 passed** (backend; 1 skipped, 1 xfailed), **42/42** frontend.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД iter29) | `pytest -q` (POSTGRES_PORT=5435, `chessannotator_iter29`, Redis 0, `STOCKFISH_PATH`, web/dist) | **671 passed, 1 skipped, 1 xfailed, 2 warnings in 326.67s**, exit 0; collected 673; skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012; **+30** к итерации 28 (641) |
| Retention unit | `pytest tests/unit/test_prune_llm_calls.py -q` | **9 passed** |
| Retention integration | `pytest tests/integration/test_prune_llm_calls.py -q` | **4 passed** |
| Дашборд unit | `pytest tests/unit/test_llmops_dashboard_unit.py -q` | **6 passed** |
| Дашборд integration | `pytest tests/integration/test_admin_llmops_dashboard.py -q` | **7 passed** |
| Аудит экспорта | `pytest tests/integration/test_admin_exports.py -q` | **15 passed** (11 прежних + 4 новых) |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 75 source files** (exit 0; +1 файл) |
| Alembic | `alembic upgrade head`, `alembic current` | head `e9f0a1b2c3d4`; новых миграций нет (14 таблиц, 10 ревизий); партиционирование — KI-052 |
| Frontend tests | `pnpm --dir web test` | **42 passed** (6 файлов), 0.78 с |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 4.77 с; JS 464.31 КБ (gzip 146.15), CSS 14.29 КБ (gzip 3.77) |

Скрипт retention отдельно в полной сюите не участвует (он не вызывается
жизненным циклом приложения и не должен — это cron-операция).

---

## 4. Smoke (ручной сценарий)

**4.1. Retention на живой БД** (`chessannotator_iter29`, PostgreSQL 5435,
сид: 3 строки `llm_calls` возрастом 400+ дней + 2 свежие):

```
python scripts/prune_llm_calls.py --dry-run --json
→ {"retention_days":90,"cutoff":"2026-07-31T14:17:07+00:00",
   "batch_size":1000,"batches":0,"deleted":3,"dry_run":true,"capped":false}

python scripts/prune_llm_calls.py --batch-size 2 --json
→ {"retention_days":90,"cutoff":"2026-07-31T14:17:08+00:00",
   "batch_size":2,"batches":2,"deleted":3,"dry_run":false,"capped":false}

python scripts/prune_llm_calls.py --dry-run
→ "удалено/будет удалено: 0 строк"

SQL: count(span_id LIKE 'smoke-old-%') = 0; count('smoke-new-%') = 2.
```

**4.2. Дашборд (живой uvicorn :8017, X-Admin-Token, БД iter29).**

```
GET /admin/llmops → 200, 54 352 байта
HTML содержит: "LLMOps-дашборд", "smoke-model", "p95",
"Топ-5 пользователей"; таблица по моделям/уровням и карточки top-5.
```

**4.3. Аудит выгрузки (JWT admin, живой сервер).**

| Запрос | Результат |
| :--- | :--- |
| `GET /v1/admin/exports/edits.jsonl?level=club&schema_version=1` (Bearer admin) | 200, `Content-Type: application/x-ndjson`, 1 строка NDJSON |
| `audit_logs` после стрима | `id=26 · jwt:smoke-admin-iter29@example.com · dataset.export · edits.jsonl · {"rows":1,"level":"club","date_from":null,"date_to":null,"human_edited":null,"schema_version":1,"completed":true,"trace_id":"5d65be9c-41ac-479a-b9e5-3ae121ac4ea8"}` |

**4.4. Full suite.** Прогон на чистой БД (drop/create +
`alembic upgrade head`) закончился `671 passed, 1 skipped, 1 xfailed`,
exit 0 (326.67 с); warnings — прежние (`Connection._cancel`,
Starlette/httpx).

---

## 5. Что не так

1. **Партиционирование `llm_calls` не сделано (KI-052).** В постановке
   итерации оно было опциональным. Postgres native partitioning требует
   включить ключ партиционирования в каждый UNIQUE/PK-индекс: текущий
   `UNIQUE (trace_id, span_id)` пришлось бы заменить на
   `(trace_id, span_id, created_at)`, а PK — на `(id, created_at)`, то
   есть пересоздать таблицу, поправить ORM-модель и миграционные тесты.
   Для MVP достаточно батчевого retention; решение зафиксировано в
   KI-052 и DESIGN §4.4.
2. **Retention не планируется приложением.** Скрипт нужно запускать
   cron/systemd timer/Task Scheduler (примеры — `GETTING_STARTED.md`
   §9.1). В `docker-compose` сервиса-планировщика нет; это инфраструктура
   развёртывания, а не код приложения.
3. **Дашборд считает по «сырому» `llm_calls`, без materialized
   aggregates.** На больших объёмах (миллионы строк) `percentile_cont` и
   `GROUP BY` за 30 дней могут быть тяжёлыми; BRIN/DESC-индексы и окно в
   30 дней сглаживают, но при росте трафика понадобятся агрегаты
   (например, `llm_metrics_daily`) или партиции (KI-052).
4. **Ошибки считаются по статусам `error`/`timeout`.** Статус `invalid`
   (ответ не прошёл валидатор) в error rate не входит — это осознанно:
   `invalid` означает проблему качества ответа, а не сбой вызова; при
   необходимости его можно добавить отдельной колонкой.
5. **Латентность исключает cache-hit.** Иначе семантические попадания
   (2–20 мс) занижали бы p95/p99 реальных вызовов LLM. На странице это
   подписано; при желании можно показывать обе метрики.
6. **Окно «24 часа» для топа пользователей — сутки UTC.** `usage_daily`
   хранит агрегаты по дням, поэтому топ-5 за 24 часа фактически означает
   «сегодня (UTC)»; для `llm_calls` окна честные (datetime).
7. **Smoke-дашборд показывает только 5 строк `llm_calls`** — после
   полной сюиты таблица почти пуста (тесты убирают свои данные); для
   проверки отрисовки этого достаточно, но «живые» цифры не отражают
   продовый объём.
8. **Аудит выгрузки — best-effort.** Запись идёт после завершения
   генератора в отдельной транзакции; при жёстком падении процесса между
   последним байтом и коммитом аудита запись может потеряться. Это
   компромисс: аудит не должен ломать уже отданную выгрузку. При обрыве
   клиента запись всё равно пишется (`completed=false`), но только если
   event loop успевает выполнить `finally`.
9. **Тестовый прогон требовал Stockfish.** На машине не оказалось
   бинарника из предыдущих итераций; для полного прогона скачан
   **Stockfish 19** (`sf_19`, `stockfish-windows-x86-64-universal.exe`,
   103 МБ) во внешний каталог `C:\Tools\stockfish` (в репозиторий не
   коммитится — KI-006). Без него engine-тесты скипаются.
10. **`audit_logs` от smoke-проверок остаются в тестовой БД.**
    Таблица append-only (триггер запрещает DELETE), поэтому smoke-запись
    `dataset.export` не удаляется; на боевом аудите это ожидаемое
    поведение, для локальной БД — безвредный мусор.
11. **Frontend не менялся.** Дашборд сделан на стороне админки
    (Jinja2), React-сборка не затрагивалась; 42 фронтенд-теста и сборка
    зелёные без изменений.
12. **`T-109` в `TODO.md` изначально означал Grafana-дашборды.** В
    постановке итерации `T-109` — LLMOps-дашборд в админке; задача
    закрыта с фактическим объёмом, OTel/Grafana-дашборды и алерты
    остаются отдельным шагом (T-113).

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| **KI-052** | Low | db, observability | `llm_calls` не партиционирована по месяцам: удержание — только батчевый `DELETE` (`prune_llm_calls.py`). Партиционирование потребует нового уникального ключа `(trace_id, span_id, created_at)` и пересоздания таблицы |

**Закрыты:** KI-048 — retention `llm_calls` реализован
(`scripts/prune_llm_calls.py`, `LLM_CALLS_RETENTION_DAYS`,
cron-инструкция); аудит `dataset.export` — часть KI-050 закрыта (сам
KI-050 остаётся Open из-за checksum/фоновой выгрузки).
Сводка реестра: **52** записи, 25 Open, 27 Closed.

---

## 7. Обоснование

- **Почему retention — отдельный CLI-скрипт, а не фоновая задача в
  приложении.** Это административная операция (как
  `aggregate_usage.py`): её не нужно запускать в каждом воркере и нельзя
  привязывать к жизненному циклу API (несколько реплик устроили бы гонку
  DELETE). Скрипт использует те же `Settings`/модели, идемпотентен,
  логирует JSON и возвращает понятный exit code — его удобно повесить на
  cron/системный планировщик; инструкция — в `GETTING_STARTED.md`.
- **Почему батчи, а не один `DELETE`.** Требование KI-048 — не
  блокировать таблицу: `DELETE` на сотни тысяч строк держит блокировки,
  раздувает WAL и откладывает autovacuum. Ограниченный подзапросом
  `LIMIT` батч + commit на итерацию даёт короткие транзакции, а
  `--sleep-ms`/`--max-batches` позволяют «мягко» убирать хвост в часы
  низкой нагрузки. `--dry-run` даёт безопасно оценить объём.
- **Почему защита `retention_days >= 1`.** Опечатка `--retention-days 0`
  при `cutoff = now` удалила бы всю таблицу; лучше явная ошибка argparse.
- **Почему процент `percentile_cont` в SQL, а не в Python.** Агрегат
  считается на стороне PostgreSQL (`percentile_cont` — оконная агрегатная
  функция), данные за 30 дней не перекачиваются в приложение; для
  админ-страницы с несколькими окнами это заметно дешевле.
- **Почему латентность без `cache_hit`.** `llm_calls` пишет и
  semantic-cache-попадания (`cache_hit=true`, latency 2–20 мс), чтобы
  видеть hit rate. Если включать их в p50/p95, перцентили перестают
  описывать реальные вызовы провайдера и «улучшаются» от роста кэша.
  Поэтому latency-агрегат фильтрует `cache_hit=false`, а cache-метрика
  считается отдельно по всей таблице.
- **Почему ошибки — `error` + `timeout`.** Это сбои вызова LLM
  (провайдер/сеть/лимит времени), они влияют на надёжность. `invalid`
  (ответ не прошёл валидатор) — метрика качества; смешивать её с
  доступностью провайдера в error rate некорректно.
- **Почему дашборд — CustomView с Jinja2, а не React-страница.**
  Админка уже starlette-admin, а страница read-only и серверная; шаблон
  в `app/templates` не тянет новую сборку фронтенда, работает с
  cookie-сессией админки и доступен `auditor`. React-SPA остаётся
  пользовательским интерфейсом, админка — операторским.
- **Почему отдельный модуль `app/llmops_dashboard.py`, а не код в
  `admin.py`.** SQL-сбор (`collect_dashboard`) не зависит от
  starlette-admin и тестируется отдельно от рендера; `admin.py` остаётся
  про аутентификацию/регистрацию views. Это же позволяет позже
  переиспользовать агрегаты (CLI/JSON-эндпоинт) без копирования.
- **Почему топ-5 — из `usage_daily`, а не из `llm_calls`.** `usage_daily`
  — уже готовый durable-агрегат по субъекту/дню (T-031b): не нужно
  сканировать сырые вызовы и группировать по `actor_id` (который для
  анонимов NULL). Цена — дневная гранулярность, поэтому окно «24 часа»
  для топа трактуется как текущие сутки UTC (подписано на странице).
- **Почему аудит выгрузки пишется в `finally` генератора.** Количество
  строк известно только после стрима; `finally` ловит и нормальное
  завершение, и обрыв клиента. Аудит — в отдельной сессии, best-effort:
  ошибка аудита логируется, но не превращает уже отданную выгрузку в
  ошибку. Флаг `completed` отличает полную выгрузку от частичной.
- **Почему актор для аудита — строка `jwt:<email>`/`session:<admin>`.**
  Формат совпадает с `actor_from_request` (`admin:<username>`/`user:<id>`)
  и не требует отдельной колонки в `audit_logs`; email — единственная
  стабильная идентичность JWT-пользователя в админке (id пользователя
  доступен, но для аудита оператора email информативнее).
- **Почему партиционирование отложено.** Оно требует изменения
  уникального ключа (`UNIQUE (trace_id, span_id)` → с `created_at`) и
  пересоздания таблицы — это отдельная миграция с риском для горячей
  таблицы. В постановке итерации 29 оно было опциональным; retention
  закрывает основной риск неограниченного роста, а партиционирование
  зафиксировано в KI-052 с планом.
