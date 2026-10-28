# Итерация 17 — Prompt Management и FinOps (T-029, T-031)

**Дата:** 2026-10-29
**Статус:** ✅ T-029/T-031 закрыты в объёме итерации; остатки вынесены в
T-029b/T-031b и KI-031/KI-032.

## 1. Файлы (+/-)

### Созданы

- `app/finops.py` — `TokenUsageTracker`, `TokenBudgetMiddleware`,
  `record_token_usage`, `compute_cost_usd`, дневное окно UTC и 429.
- `alembic/versions/e3f4a5b6c7d8_add_prompts.py` — таблица `prompts`.
- `tests/unit/test_token_budget.py` — 9 тестов счётчиков/middleware.
- `tests/integration/test_token_budget_api.py` — 429 в реальном app.
- `tests/integration/test_prompts_db.py` — правка БД + сброс кэша, seed.
- `tests/unit/test_prompt_admin.py` — версия и инвалидация в хуках.
- `docs/development/reports/v0.1.0/ITERATION-17.md` — этот отчёт.
- `docs/development/reports/v0.1.0/ITERATION-17_prompt_smoke.log` —
  ручная правка промпта через `/admin`.
- `docs/development/reports/v0.1.0/ITERATION-17_token_budget_smoke.log` —
  200/200/429 и счётчик Redis.

### Изменены

- `app/db/models.py` — модель `Prompt` (`level` UNIQUE, `content`,
  `version`, `updated_at`).
- `app/prompts/__init__.py` — async `load_system_prompt` (кэш → БД →
  файл), `reset_prompt_cache`, `seed_prompts`, `warm_prompt_cache`.
- `app/admin.py` — редактируемый `PromptAdmin`: только `content`,
  версия инкрементируется в `before_edit`, кэш сбрасывается в
  `after_edit_committed`.
- `app/main.py` — seed/прогрев промптов в lifespan, `TokenUsageTracker`
  и `TokenBudgetMiddleware`.
- `app/config.py` — `llm_local_price_*`, облачные `llm_price_*`,
  `token_budget_enabled`, `daily_token_limit_free/pro`.
- `app/llm/base.py` — `LLMUsage.cost_usd`.
- `app/llm/openai_client.py` — стоимость по ценам, запись токенов в
  FinOps, оценка токенов/стоимости для SSE.
- `app/llm/factory.py` — локальные/облачные цены по уровню.
- `app/api/v1/commentary.py` — `commentaries.cost_usd` из `LLMUsage`.
- `app/commentary/service.py`, `app/commentary/streaming.py` — async
  загрузка промпта; стоимость SSE-комментария.
- `tests/conftest.py` — `TOKEN_BUDGET_ENABLED=false` для интеграционных
  тестов (бюджет проверяется отдельно).
- `tests/test_models.py` — `prompts` в SPEC_TABLES, тест constraints.
- `tests/unit/test_prompts.py`, `tests/integration/test_generation.py` —
  `await load_system_prompt`.
- `tests/unit/test_llm.py`, `tests/unit/test_observability.py` —
  проверка `cost_usd`.
- `tests/integration/test_admin.py` — view промптов и edit-страница.
- `scripts/evaluate_llm.py` — в eval бюджет отключён.
- `.env.example`, `docs/development/SPEC.md`, `TODO.md`,
  `docs/KNOWN_ISSUES.md`, `docs/CHANGELOG.md`, `README.md`,
  `docs/development/RELEASES.md`.

## 2. Покрытие

| Область | Тесты | Что проверяют |
| :--- | :--- | :--- |
| Token budget | `tests/unit/test_token_budget.py` (9) | INCRBY/TTL, лимит, 429+заголовки, free/pro, fail-open, no-op вне запроса, запись токенов самим LLM-клиентом |
| Token budget API | `tests/integration/test_token_budget_api.py` (1) | middleware подключён к `/v1/analyze`, 429 до эндпоинта |
| Prompt DB/cache | `tests/integration/test_prompts_db.py` (2) | правка строки + `reset_prompt_cache` → новый текст; seed idempotent |
| Prompt admin | `tests/unit/test_prompt_admin.py` (3) | `before_edit` +1 версия; `after_edit_committed` сбрасывает кэш; пустой `content` отклоняется |
| Prompt view | `tests/integration/test_admin.py` (+1) | `/admin/prompt/list` и `/admin/prompt/edit?pk=` доступны, есть `content` |
| Схема | `tests/test_models.py` (+1) | `prompts` в метаданных, UNIQUE `level`, NOT NULL |
| Стоимость | `tests/unit/test_llm.py`, `test_observability.py` | `usage.cost_usd` по ценам, OTel-атрибут |

Итого: **+17 тестов** к 218 → **235 passed, 1 xfailed**.

## 3. Результаты

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Lint | `ruff check .` | **0** ошибок |
| Types | `mypy app` | **0** ошибок, 52 файла |
| Tests | `pytest -q` | **235 passed, 1 xfailed** (182.79 с) |
| Build | backend | **0/0** (ruff/mypy); фронтенд не менялся |
| Migration | `alembic upgrade head` | `d2e3f4a5b6c7 → e3f4a5b6c7d8` |
| Drift | `alembic check` | «No new upgrade operations detected» |
| Prompt smoke | `ITERATION-17_prompt_smoke.log` | POST 303, version +1, кэш отдал новый текст |
| Token smoke | `ITERATION-17_token_budget_smoke.log` | 200 / 200 / 429, счётчик 120 |

Таблиц в БД: **9** (добавлена `prompts`). Head: `e3f4a5b6c7d8`.

## 4. Smoke

### Редактирование промпта через `/admin`

Сценарий: открыть `/admin/prompt/edit?pk=2` с `X-Admin-Token`, отправить
новый `content` с CSRF-токеном, проверить версию в БД и что следующий
`load_system_prompt('club')` **без ручного reset** вернул новый текст.

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-17_prompt_smoke.log`:

```text
before: prompt id=2, version=1, chars=2354
GET /admin/prompt/edit?pk=2 -> 200, csrf=True
prompt_updated version=2
POST /admin/prompt/edit?pk=2 -> 303 http://testserver/admin/prompt/list
after DB: version=2 (+1), content_has_marker=True
load_system_prompt('club') without manual reset: has_marker=True
restored: version=1, equals_original=True
```

### Лимит токенов

Сценарий: лимит free = 100, тестовый endpoint «тратит» 60 токенов за
запрос. Первые два запроса проходят, третий — 429.

Фактический вывод — `docs/development/reports/v0.1.0/ITERATION-17_token_budget_smoke.log`:

```text
request 1: HTTP 200
request 2: HTTP 200
token_budget_exceeded limit=100 subject=ip:testclient tier=free used=120
request 3: HTTP 429 {'code': 'token_budget_exceeded'}
  X-RateLimit-Reset: 1793232000
  X-RateLimit-Limit: 100
  X-Token-Budget-Period: day
redis key usage:2026-10-28:ip:testclient = 120 tokens, ttl=6685 s
```

> Ключ датирован UTC: локально 29 октября, в UTC ещё 28-е. Сброс — в
> ближайшую полночь UTC, `X-RateLimit-Reset` — epoch-секунды.

## 5. Что не так

- **Prompt-версии.** В таблице одна строка на уровень: правка перезаписывает
  `content` и инкрементирует `version`, но старые версии не хранятся.
  Нет rollback, diff/истории, `GET/PUT /v1/admin/prompts` и аудита
  (`T-029b`, `T-035`).
- **Мультипроцессный кэш.** `reset_prompt_cache` действует только в
  текущем процессе; при нескольких воркерах нужен Redis Pub/Sub
  (`KI-032`).
- **Тир бюджета.** `X-User-Tier: pro` не аутентифицирован — клиент может
  подделать лимит (`KI-031`). Subject тоже доверяет `X-Forwarded-For`; в
  production нужен доверенный прокси/JWT.
- **FinOps-остаток.** Нет durable `usage_daily`, месячных/глобальных
  лимитов, reserve/reconcile, админ-настройки лимитов и алертов
  (`T-031b`). Успешные ответы не получают `X-Token-Budget-*` — только 429.
- **Цена.** Прайс общий для облака и задаётся ENV, а не per-model
  `price_book`; локальная цена 0. Смена цены не фиксируется в истории
  вызова (`KI-021` закрыт, per-model — `T-031b`).
- **Async-сигнатура.** `load_system_prompt` стал async; все вызовы
  обновлены, но внешние скрипты, импортирующие его синхронно, сломаются.
- **БД при старте.** Если `prompts` недоступна, seed/прогрев пишут
  warning, приложение работает на `.txt`-файлах — это осознанный
  fallback, а не «тихое» скрытие ошибки.
- **CI.** GitHub Actions не запускался (нет push); локально `ruff`,
  `mypy`, `pytest`, `alembic check` зелёные.

## 6. Новые KI

- **KI-031** · Open · Med · api, finops, security — тир `free/pro`
  берётся из `X-User-Tier` без аутентификации.
- **KI-032** · Open · Med · prompts, admin, infra — инвалидация кэша
  промптов только в текущем процессе; multi-worker нужен Redis Pub/Sub.
- **KI-021** закрыт: `cost_usd` считается по конфигу, пишется в
  `commentaries` и OTel-спан.

## 7. Обоснование

- **Redis для счётчиков.** Проверка бюджета должна быть дешёвой,
  атомарной и общей для всех воркеров. `INCRBY` + TTL до UTC-полуночи
  даёт это без транзакций БД; при недоступном Redis middleware
  fail-open, как существующий rate limiting (KI-015). Durable-агрегаты
  и точный reserve/reconcile — T-031b, иначе горячий путь усложняется
  раньше времени.
- **Кэш промптов.** Источник истины — `prompts`; in-memory dict — горячий
  путь. `load_system_prompt` async, потому что при промахе кэша нужен
  запрос в БД; на старте кэш прогревается, после правки сбрасывается в
  `after_edit_committed` (после commit, а не до). Seed использует
  `INSERT … ON CONFLICT (level) DO NOTHING`, поэтому одновременный старт
  нескольких воркеров не падает на UNIQUE. При недоступной БД —
  fallback на `.txt`, чтобы генерация не падала. Мультипроцессная
  инвалидация — T-029b/KI-032.
- **`PromptAdmin` только `content`.** `level` — ключ, `version` и
  `updated_at` — серверные поля: клиент не может подделать версию или
  переименовать уровень. Create/delete отключены: seed и фиксированный
  набор уровней.
- **Стоимость в `LLMUsage`.** Клиент — единственное место, где известны
  точные токены и провайдер; он же считает `cost_usd`, OTel-спан и API
  берут одно и то же значение. Это закрывает KI-021 без дублирования
  формулы в трёх слоях.
- **429 с `X-RateLimit-Reset`.** Отдельный код `token_budget_exceeded`
  отличает бюджет от RPM-лимита; `Retry-After` + epoch-сброс дают
  клиенту понять, когда повторить. Middleware стоит внутри CORS и снаружи
  RPM-лимитера, поэтому 429 виден браузеру и не тратит RPM-счётчик.
