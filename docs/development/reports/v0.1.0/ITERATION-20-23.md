# Ночная смена — итерации 20–23 (T-036…T-040)

**Дата:** 2026-10-29
**Статус:** ✅ история/лимиты, mobile/skeleton/Error Boundary,
LLM-evals в админке, CI caching; все изменения закоммичены.

## 1. Файлы (+/-)

### Frontend

- `web/src/App.tsx` — React Router, shell, Error Boundary.
- `web/src/components/Layout.tsx`, `UsageBadge.tsx`,
  `AnalysisWorkspace.tsx`, `Skeleton.tsx`, `ErrorBoundary.tsx`.
- `web/src/pages/HomePage.tsx`, `HistoryPage.tsx`, `AnalysisPage.tsx`.
- `web/src/stores/chatStore.ts` — Zustand + `sessionStorage`.
- `web/src/components/ChatPanel.tsx` — история чата и передача `history`.
- `web/src/api.ts`, `types.ts` — `getGame`, `listGames`, `getUsage`,
  `ChatHistoryItem`; `streamChat(..., history)`.
- `web/src/i18n.ts`, `styles.css` — RU/EN, mobile 1280/820/640/480,
  skeleton/modal/history/usage.
- `web/package.json`, `pnpm-lock.yaml` — `react-router-dom`.

### Backend

- `app/api/v1/games.py` — `GET /v1/games`.
- `app/api/v1/users.py` — `GET /v1/users/me/usage`.
- `app/api/v1/chat.py` — `history`, повторный guardrail.
- `app/chat/service.py`, `streaming.py` — история в промпте и `prompt_hash`.
- `app/commentary/service.py`, `streaming.py` — app settings в API-путях.
- `app/finops.py` — `request_subject`/`request_tier`.
- `app/web.py` — SPA fallback (`/history`, `/analysis/:id`).
- `app/main.py` — users router, `app.state.settings`, SPA mount.
- `app/db/models.py` — `LlmEvaluation`.
- `alembic/versions/b6c7d8e9f0a1_add_llm_evaluations.py`.
- `app/admin.py` — `LlmEvaluationAdmin`.
- `scripts/evaluate_llm.py` — запись в `llm_evaluations`.
- `.github/workflows/ci.yml` — pip/pnpm cache, `workflow_dispatch`.

### Тесты

`tests/integration/test_api_usage.py`, `test_llm_evaluations.py`,
`test_spa.py`; дополнены `test_api_games.py`, `test_api_chat.py`,
`test_admin.py`, `test_models.py`; `tests/unit/test_chat_history.py`.
Отчёт и smoke-логи:
`docs/development/reports/v0.1.0/ITERATION-20-23.md`,
`ITERATION-20-23_llm_eval_smoke.log`.

## 2. Покрытие

| Область | Тесты | Что проверяют |
| :--- | :--- | :--- |
| История | `test_api_games.py` (+1) | `/v1/games` отдаёт white/black/event/level |
| Лимиты | `test_api_usage.py` (2) | tracker values и fallback при выключенном бюджете |
| История чата | `test_chat_history.py` (3) | формат, промпт, зависимость `prompt_hash` |
| Injection в истории | `test_api_chat.py` (+1) | user-реплики истории блокируются 422 |
| SPA | `test_spa.py` (2) | `/history`, `/analysis/:id` → index; `/v1/*` → 404 |
| LLM evals | `test_llm_evaluations.py` (1) | `_save_evaluations` пишет 3 шкалы |
| Схема | `test_models.py` (+1) | `llm_evaluations` и NOT NULL |
| Админка | `test_admin.py` | read-only view `/admin/llm-evaluation/list` |
| Frontend | 20 Vitest | существующие FEN/SSE/формат-тесты; новых UI-тестов нет |

Итого: **+11 backend-тестов** к 275 → **286 passed, 1 xfailed**;
frontend 20 passed, `typecheck`/`build` зелёные.

## 3. Результаты

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Lint | `ruff check app scripts tests` | **0** |
| Types | `mypy app` | **0**, 60 файлов |
| Backend | `pytest -q` | **286 passed, 1 xfailed** (193.14 с) |
| Frontend tests | `pnpm --dir web test` | **20 passed** |
| Frontend types | `pnpm --dir web typecheck` | **0** |
| Frontend build | `pnpm --dir web build` | **OK**, 96 modules, 455.86 kB JS |
| Migration | `alembic upgrade head` | head `b6c7d8e9f0a1` |
| Drift | `alembic check` | без дрейфа |
| Docker | `docker build -t chessannotator:night-shift .` | **OK**, image `124adc88b5b4`, 527 MB |
| CI YAML | `yaml.safe_load` | 3 jobs, шаги на месте |

**Коммиты:**

| Этап | Хеш |
| :--- | :--- |
| Stage 1 — history/usage/chat | `12fd708` |
| Stage 2 — mobile/skeleton/error boundary | `3901448` |
| Stage 3 — llm_evaluations/admin | `6051931` |
| Stage 4 — CI caching | `dcdacfc` |
| Fix — SPA + history guard | `bd8b444` |
| Fix — settings в generation API | `abfa15a` |

## 4. Smoke

**SPA (production static):**

```text
GET /history       -> 200, index.html
GET /analysis/42   -> 200, index.html
GET /v1/definitely-missing -> 404
```

**LLM-eval → БД** (`ITERATION-20-23_llm_eval_smoke.log`):

```text
ANALYZE Stein, Leonid — Geller, Efim: 200 (14.7 с)
COMMENTARY ... club: 200, 40.0 с
llm_completed model=deepseek-flash tokens_in=1237 tokens_out=86
[1/1] ... club: {'factual_accuracy': 3.0, 'usefulness': 3.0, 'style': 4.0}
Сохранено в llm_evaluations: 1
Средняя: 3.33/10
```

**Docker:**

```text
chessannotator:night-shift 124adc88b5b4 527MB
```

**Frontend build:**

```text
96 modules transformed
dist/assets/index-*.js 455.86 kB │ gzip: 143.64 kB
✓ built in 5.65s
```

## 5. Что не так

- **KI-035 (High):** `GET /v1/games` и `/history` отдают все партии —
  нет auth/владельца. Для локального MVP допустимо, для multi-user нет;
  план — T-028 (JWT, `user_id`, фильтр по субъекту).
- **Нет browser E2E.** Проверены сборка, типы, SPA-ответы и API; вживую
  мобильная вёрстка/клики в браузере не прокликаны (Playwright не
  установлен). CSS breakpoints 480/640/820/1280.
- **Usage badge зависит от Redis.** Если `TOKEN_BUDGET_ENABLED=false` или
  Redis недоступен, бейдж скрыт (`enabled=false`). Pro-модалка — текст,
  покупки/апгрейда нет.
- **История чата — client-side.** Zustand + `sessionStorage`: живёт до
  закрытия вкладки, не синхронизируется между устройствами. Сервер
  хранит только `commentaries`, не диалоги; история ограничена 20
  репликами и повторно проверяется guardrails.
- **LLM-evals — только таблица.** Нет графиков/фильтров/CSV (T-039b);
  read-only view сортируется, но не агрегирует. Средняя 3.33/10 у
  smoke-партии — качество модели остаётся низким (KI-030).
- **CI не запускался в GitHub Actions.** Локально проверены YAML,
  ruff/mypy/pytest/typecheck/build; реальный runner/кэш не проверен.
- **Docker только собран.** Контейнер не поднимался с Postgres/Redis;
  runtime-проверка ограничена build.
- **`evaluate_llm` сохранение best-effort.** При ошибке БД печатает
  warning и продолжает MD/JSON; строки могут не появиться.
- **`GET /v1/games` парсит PGN на каждый ряд.** Для 20 строк ок; при
  росте лимита нужен денормализованный white/black/event.
- **Нет пагинации истории.** Только последние 20; «показать ещё» нет.

## 6. Новые KI

- **KI-035** · Open · High · web, api — глобальная история без
  auth/скоупа; перебор чужих партий через `/v1/games` и `/analysis/:id`.
- **KI-034** · Open · Med — semantic-хит по близкому FEN (итерация 19).
- **KI-030** · Open · Med — качество LLM ниже 7/10 (средняя 3.33/10 в
  smoke).
- Новых KI по SPA/chat history нет: fallback и повторный guardrail
  закрыты тестами.

## 7. Обоснование

- **История без миграции.** `GET /v1/games` читает `games` и парсит PGN
  через существующий `parse_pgn`; white/black/event не дублируются в БД.
  Лимит 20 — требование задачи, без пагинации.
- **Лимиты без JWT.** `request_subject`/`request_tier` переиспользуют
  логику `TokenBudgetMiddleware` (IP + `PRO_IPS`), поэтому бейдж и 429
  считают один и тот же бюджет. `app.state.settings` гарантирует, что
  API-пути используют настройки приложения, а не глобальный кэш.
- **История чата.** `sessionStorage` выбран вместо `localStorage`, чтобы
  диалог не переживал закрытие вкладки; `prompt_hash` включает
  `history`, поэтому кэш не переиспользует ответ для другого контекста.
  User-реплики истории проходят `inspect_user_input` повторно — иначе
  injection можно спрятать в предыдущем сообщении.
- **Mobile/skeleton/Error Boundary.** CSS mobile-first breakpoints,
  `Skeleton` с shimmer и `role=status`, Error Boundary с reload.
  `AnalysisWorkspace` вынесен, чтобы главная и `/analysis/:id` не
  дублировали разметку.
- **SPA fallback.** `StaticFiles(html=True)` не отдаёт index для
  `/history`; `SPAStaticFiles` возвращает index только для путей без
  расширения и не для `/v1`, `/admin`, `/docs`, `/openapi`.
- **llm_evaluations.** `Numeric(4,2)` вместо `Float`, потому что
  starlette-admin не конвертирует `FLOAT` автоматически; read-only
  view сохраняет аудит-подход. `evaluate_llm.py` пишет в БД
  best-effort, чтобы сбой таблицы не ломал MD/JSON-отчёт.
- **CI caching.** `setup-python` с `cache: pip`, `setup-node` с
  `cache: pnpm` и явный `actions/cache` для pnpm store; добавлен
  `workflow_dispatch` и `permissions: contents: read`. Полный набор
  шагов уже был, кэш ускоряет установку.
- **Коммиты.** Четыре stage-коммита по сообщениям задания плюс два
  fix-коммита (SPA/guardrails и settings propagation); рабочее дерево
  чистое, готово к `git push`.
