# TODO — ChessAnnotator

Легенда: `[ ]` — не сделано, `[x]` — сделано, `[~]` — в работе.
Каждая задача: ID, заголовок, зависимости, Definition of Done (DoD),
оценка (в человеко-днях, ориентир).

Работай сверху вниз. Не перескакивай через зависимости.

---

## Фаза 0. Foundation (1 неделя)

### [x] T-001. Инициализация репозитория
- **Зависит от:** —
- **Что:** создать структуру репо (`app/`, `web/`, `docs/`, `tests/`,
  `.github/workflows/`), `pyproject.toml`, `web/package.json`,
  `.env.example`, `.gitignore`, `.editorconfig`, `README.md` (готовый),
  `docs/` (готовый), лицензию.
- **DoD:** `git clone && pip install -e ".[dev]" && pnpm --dir web install`
  проходят без ошибок; CI-скелет зелёный (build, typecheck, tests —
  заглушки проходят).
- **Оценка:** 0.5

### [x] T-002. Docker Compose (dev)
- **Зависит от:** T-001
- **Что:** `docker-compose.yml` с сервисами `postgres` (pgvector),
  `redis`, `stockfish`, `vllm` (опционально, профиль `llm-local`).
- **DoD:** `docker compose up -d postgres redis stockfish` поднимает всё,
  healthcheck'и зелёные, порты проброшены на loopback.
- **Оценка:** 0.5

### [x] T-003. Базовый FastAPI + healthcheck
- **Зависит от:** T-001, T-002
- **Что:** `app/main.py`, `app/config.py` (pydantic-settings),
  `/v1/health` (проверяет БД, Redis, движок, LLM).
- **DoD:** `uvicorn app.main:app` поднимается; `/v1/health` возвращает
  статусы всех зависимостей; unit-тест на healthcheck.
- **Оценка:** 0.5

### [x] T-004. SQLAlchemy + Alembic + начальная миграция
- **Зависит от:** T-002, T-003
- **Что:** модели из SPEC.md §4 (users, games, moves, motifs,
  annotated_games, retrieval_cache, commentaries, edits),
  `alembic upgrade head`.
- **DoD:** миграция применяется на чистой БД; `alembic downgrade base` и
  повторный upgrade проходят; тест на миграции в CI.
- **Оценка:** 1

### [x] T-005. CI (GitHub Actions)
- **Зависит от:** T-001…T-004
- **Что:** workflow `ci.yml`: `ruff`, `mypy`, `pytest`, `alembic upgrade`,
  `pnpm --dir web build`, `pnpm --dir web test`.
- **DoD:** CI зелёный на `main`; PR-чек обязателен.
- **Оценка:** 0.5

### [x] T-006. Базовые документы из `docs/`
- **Зависит от:** T-001
- **Что:** создать `docs/development/RULES.md`,
  `docs/development/PROMPT_CURRENT.md` (копия PROMPT_FOR_DSH),
  `docs/development/RELEASES.md` (шаблон отчёта итерации),
  `docs/CHANGELOG.md` (v0.1.0 — Unreleased),
  `docs/KNOWN_ISSUES.md` (KI-001 — заведён).
- **DoD:** документы созданы, ссылки из `docs/README.md` работают.
- **Оценка:** 0.5

---

## Фаза 1. Ingestion + Engine (1 неделя)

### [x] T-007. Ingestion: PGN/FEN → нормализованная партия
- **Зависит от:** T-004
- **Что:** `app/ingestion/` на `python-chess`: парсинг PGN, валидация,
  ECO-код, детект транспозиций, разметка фаз.
- **DoD:** 10 тестовых PGN (включая `1.d4 Nf6 2.c4 e6 3.Nc3 Bb4 ...
  27.Bg6+ 1-0`) разбираются корректно; unit-тесты на все ветки.
- **Оценка:** 1.5

### [x] T-008. Engine Service: Stockfish-обёртка
- **Зависит от:** T-002, T-007
- **Что:** `app/engine/` — async-обёртка над Stockfish (через
  `stockfish` py или UCI-сокет), MultiPV, глубина по уровню, классификация
  ходов (blunder/mistake/inaccuracy/good/best/brilliant).
- **DoD:** для тестовой партии возвращает evals, best move, delta,
  classification; unit-тесты на классификацию; бенчмарк латентности
  (клуб: ≤1с/ход).
- **Оценка:** 2

### [x] T-009. `POST /v1/analyze`
- **Зависит от:** T-007, T-008
- **Что:** эндпоинт принимает PGN/FEN + `level`, возвращает
  структурированный JSON (moves, evals, motifs — пока без мотивов,
  заглушка), сохраняет в БД, возвращает `analysis_id`.
- **DoD:** e2e-тест: PGN → JSON с корректными evals; OpenAPI-схема
  сгенерирована; rate limiting подключён.
- **Оценка:** 1

---

## Фаза 2. Motif Detector + Policy Layer (1 неделя)

### [x] T-010. Motif Detector
- **Зависит от:** T-008
- **Что:** эвристики + фичи движка → структурированные мотивы
  (слабые поля, проходные, открытые линии, безопасность короля,
  тактические темы). Привязка к ходам.
- **DoD:** на 20 тестовых позициях детектор находит ожидаемые мотивы
  (recall ≥ 80%); unit-тесты по каждому типу мотива.
- **Оценка:** 2

### [x] T-011. Policy Layer (3 уровня)
- **Зависит от:** T-008, T-010
- **Что:** детерминированная функция выбора рекомендуемого хода под
  уровень: новичок — не зевок + принцип; клуб — top-3 + мотивы;
  гроссмейстер — top-line + стратегия.
- **DoD:** unit-тесты на все три уровня; на тестовом наборе политика
  не выбирает зевков для новичка (0 случаев delta > 250 cp).
- **Оценка:** 2

### [x] T-012. Расширить `/v1/analyze` мотивами и policy
- **Зависит от:** T-010, T-011
- **Что:** `/v1/analyze` возвращает motifs и `recommended_move` под
  уровень.
- **DoD:** e2e-тест проверяет наличие motifs и recommended_move для
  каждого уровня.
- **Оценка:** 0.5

---

## Фаза 3. LLM Generation + Validator (1 неделя)

### [x] T-013. LLM-адаптер (локальный + облачный)
- **Зависит от:** T-003
- **Что:** `app/llm/` — единый интерфейс `generate(prompt, level)`,
  провайдеры: vLLM (Qwen3/Llama), DeepSeek, OpenAI, Anthropic; выбор
  по уровню; retry с экспоненциальной задержкой; учёт токенов и
  стоимости.
- **DoD:** unit-тесты с моками; живой тест с локальной LLM;
  метрики токенов/стоимости в БД.
- **Оценка:** 2

### [x] T-014. Системные промпты (3 уровня)
- **Зависит от:** T-013
- **Что:** `app/prompts/beginner.txt`, `club.txt`, `gm.txt` из SPEC.md
  §6; сборка «пакета фактов» из анализа.
- **DoD:** unit-тест: пакет фактов → промпт содержит все обязательные
  поля; ручная проверка 3 промптов.
- **Оценка:** 1

### [x] T-015. Validator: проверка всех вариантов из LLM
- **Зависит от:** T-008, T-013
- **Что:** парсер вариантов из LLM-ответа → прогон через Stockfish →
  отбрасывание нелегальных/ошибочных → retry LLM при необходимости.
- **DoD:** unit-тесты на все классы ошибок (нелегальный ход, зевок в
  варианте, отсутствующий вариант); e2e: 100% вариантов в ответе
  легальны и валидны.
- **Оценка:** 2

### [x] T-016. `POST /v1/commentary`
- **Зависит от:** T-012, T-014, T-015
- **Что:** эндпоинт принимает `analysis_id` или PGN + `level` +
  `style`, возвращает сгенерированный и провалидированный комментарий,
  сохраняет в БД.
- **DoD:** e2e-тест на 5 партиях × 3 уровня; сохранение в
  `commentaries`; метрики токенов/стоимости.
- **Оценка:** 1.5

---

## Фаза 4. RAG (1 неделя)

### [x] T-017. Схема RAG + ingest 100–200 аннотированных партий
- **Зависит от:** T-004
- **Что:** `annotated_games` с эмбеддингами (`bge-m3` или
  `text-embedding-3-large`); скрипт ingest; админ-эндпоинт
  `POST /v1/ingest`.
- **DoD:** 100–200 партий загружены; retrieval возвращает top-k
  релевантных аннотаций на тестовых запросах (ручная оценка ≥ 70%
  релевантности).
- **Оценка:** 2.5

### [ ] T-018. Retrieval-слой в `/v1/commentary`
- **Зависит от:** T-016, T-017
- **Что:** для каждого критического хода — top-k аннотаций из базы →
  в «пакет фактов» для LLM (влияет на промпт гроссмейстерского уровня
  сильнее, чем на новичка).
- **DoD:** e2e-тест: при наличии retrieved-контекста промпт содержит
  его; для новичка retrieval опционален.
- **Оценка:** 1.5

---

## Фаза 5. Frontend + Chat + Human-in-the-loop (1 неделя)

### [x] T-019. Скелет React-приложения
- **Зависит от:** T-001
- **Что:** Vite + React 19 + Tailwind + react-i18next + Zustand;
  роутинг; layout; страницы «Загрузить партию», «Анализ», «Чат».
- **DoD:** SPA собирается; прокси на backend работает; smoke-тест
  Playwright.
- **Статус итерации 15:** SPA на React 19 + Vite собирается, Vite-прокси
  `/v1` работает, layout одностраничный; добавлены `react-chessboard`,
  `chess.js`, `react-i18next` (RU/EN), Zustand+sessionStorage. Playwright
  не внедрён.
- **Оценка:** 1.5

### [~] T-020. Загрузка PGN + отображение анализа
- **Зависит от:** T-009, T-019
- **Что:** форма загрузки PGN/FEN, выбор уровня, вызов `/v1/analyze`,
  отображение ходов, evals, motifs, recommended_move.
- **DoD:** e2e: PGN → граф партии с подсветкой критических моментов;
  скриншот-тест.
- **Статус итерации 14:** форма, уровень, доска, список ходов, evals,
  мотивы и рекомендация реализованы; вместо графа — список ходов,
  скриншот-теста нет.
- **Оценка:** 2
### [x] T-021. Отображение комментария + правка
- **Зависит от:** T-016, T-020
- **Что:** панель комментария, стриминг через SSE, inline-редактор,
  `PATCH /v1/commentaries/{id}`.
- **DoD:** e2e: комментарий появляется, правится, сохраняется; правка
  попадает в `edits` (датасет).
- **Статус итерации 15:** панель комментария и inline-редактор готовы,
  PATCH пишет в `edits`, SSE-стриминг реализован (`/commentary/stream`),
  интеграционные тесты зелёные.
- **Оценка:** 1.5

#### [x] T-021b. `PATCH /v1/commentaries/{id}` + датасет правок
- **Зависит от:** T-016
- **Что:** обновление `commentaries.text`, `human_edited = true`, запись
  пары «оригинал → правка» в `edits`; ответ — обновлённый комментарий.
- **DoD:** тест: создать комментарий → PATCH → `human_edited=true` и запись
  в `edits`; повторный PATCH добавляет вторую запись; 404/422 на ошибки.
- **Оценка:** 0.5

### [x] T-022. `POST /v1/chat` + чат-клиент
- **Зависит от:** T-016, T-021
- **Что:** эндпоинт чата по `analysis_id`, SSE-стриминг ответа, та же
  валидация вариантов, что и в `/v1/commentary`.
- **DoD:** e2e: пользователь задаёт вопрос → получает валидированный
  ответ; 100% вариантов легальны.
- **Статус итерации 15:** серверный JSON-эндпоинт, React-чат-клиент и
  SSE (`/chat/stream`) готовы; контекст из анализа, LLM по уровню,
  валидатор с retry, typewriter-эффект.
- **Оценка:** 2
- **Оценка:** 2

---

## Фаза 6. Качество, метрики, релиз MVP (1 неделя)

### [x] T-023. Тестовый набор 30–50 партий + LLM-as-judge
- **Зависит от:** T-016, T-018, T-022
- **Что:** 30–50 партий с эталонными комментариями; LLM-as-judge
  сравнение; метрика стиля (не ниже 7/10), фактической точности (100%
  валидация), покрытия ключевых моментов (recall ≥ 95%).
- **DoD:** отчёт в `docs/development/RELEASES.md`; все метрики
  достигнуты.
- **Статус итерации 14:** выполнен LLM-as-judge на 5 партиях × 2 уровня
  (`scripts/evaluate_llm.py`, отчёт `docs/development/reports/LLM_EVAL.md`).
  Валидация — 100%; средняя оценка ниже 7/10, заведён KI-030. Полный
  набор 30–50 партий и recall ключевых моментов не измерены.
- **Оценка:** 2

### [x] T-024. Слепой тест уровней
- **Зависит от:** T-016
- **Что:** 10 шахматистов разного уровня, слепой тест: правильно ли
  определяют целевой уровень комментария (≥ 80%).
- **DoD:** отчёт с результатами.
- **Статус итерации 35:** инструмент готов — `scripts/generate_blind_test.py`
  берёт 3 партии с анализом, генерирует комментарии трёх уровней и
  раскладывает их по слотам `A`/`B`/`C` (перемешивание `--seed`), пишет
  `answer_form.md` и `answers_key.json` для организатора; `--dry-run`
  работает без LLM. **Сам живой тест с людьми не проведён** (нужны 10
  участников) — выполняется вручную; отчёт с результатами остаётся
  открытым пунктом вне релизного блока v1.0.0.
- **Оценка:** 1

### [x] T-025. Docker production + GHCR
- **Зависит от:** T-019…T-022
- **Что:** `docker-compose.prod.yml`, multi-stage Dockerfile,
  публикация в GHCR (`latest`, `sha-*`, `vX.Y.Z`).
- **DoD:** `docker compose -f docker-compose.prod.yml up -d --build`
  поднимает весь стек; smoke-тест e2e на прод-сборке.
- **Статус итерации 14:** multi-stage образ (Node → Python + SPA),
  `docker-compose.prod.yml` с loopback-портами БД/Redis, healthcheck;
  образ собран, стек поднят, `/v1/health` = `ok`, `/` отдаёт SPA.
  Публикация в GHCR не выполнялась (нет registry-доступа).
- **Оценка:** 1.5

### [x] T-026. Документация MVP
- **Зависит от:** T-023, T-024, T-025
- **Что:** финализировать `USER_GUIDE.md`, `ASSISTANT_LEVELS.md`,
  `ARCHITECTURE.md`, `GETTING_STARTED.md`, `MANUAL_TESTING_GUIDE.md`,
  `CHANGELOG.md` (v0.1.0), `KNOWN_ISSUES.md`.
- **DoD:** все ссылки в `docs/README.md` работают; README обновлён.
- **Статус итерации 14:** `USER_GUIDE.md`, `ASSISTANT_LEVELS.md`,
  `ARCHITECTURE.md`, `MANUAL_TESTING_GUIDE.md` созданы; README,
  CHANGELOG, RELEASES и GETTING_STARTED обновлены; ссылки проверены.
- **Оценка:** 1

### [x] T-027. Релиз v0.1.0
- **Зависит от:** T-023…T-026
- **Что:** чек-лист из `RELEASES.md`, тег `v0.1.0`, публикация образа,
  релиз-ноты.
- **DoD:** тег создан, образ в GHCR, релиз-ноты опубликованы.
- **Статус итерации 14:** релизные артефакты (CHANGELOG 0.1.0, README,
  RELEASES, LLM_EVAL) готовы; тег и GHCR не созданы — нет доступа к
  registry и не закрыт T-024 (слепой тест).
- **Статус итерации 34 (T-046):** job `publish-docker` в CI
  (`docker/login-action`, `metadata-action`, `build-push-action`) пушит
  `ghcr.io/iilmchat/ai-chess-annotator:vX.Y.Z` и `:latest` по тегу `v*`;
  build-arg `VITE_API_BASE` проброшен. Фактический `git tag`/push —
  действие мейнтейнера при релизе.
- **Статус итерации 35:** релизные артефакты v1.0.0 готовы (CHANGELOG
  `[1.0.0]`, README, RELEASES, версии в `pyproject.toml`/`app/__init__.py`/
  `web/package.json`); тег `v1.0.0` и публикация образа в GHCR — действие
  мейнтейнера (в этой сессии не выполнялись).
- **Оценка:** 0.5

---

## Фаза 7. Production Hardening & Admin Panel (4–5 недель)

Цель фазы — превратить MVP в управляемый production-сервис: защищённая
админ-панель, версионируемые промпты, сквозная наблюдаемость LLM,
контроль стоимости, кэширование и защита от prompt injection. Порядок
задач — сверху вниз: T-028 задаёт control plane, остальные на него
опираются.

### [x] T-028. Admin Control Plane (итерации 16, 24, 26)
- **Зависит от:** T-004, T-025, T-027
- **Статус итерации 16:** `/admin` смонтирован, защищён `X-Admin-Token`
  / cookie-сессией, подключены read-only views `annotated_games`,
  `commentaries`, `edits`; тесты `tests/integration/test_admin.py`.
- **Статус итерации 24:** роли `user/admin/auditor`, `users.role`,
  `password_hash`, `is_active`, `last_login_at` добавлены миграцией
  `c7d8e9f0a1b2`; JWT-аутентификация публичного API — T-041.
- **Статус итерации 26 (финал JWT-интеграции):** `AuthProvider` и
  `AdminAuthGuard` принимают access JWT (`Authorization: Bearer` или
  cookie `access_token`): `admin`/`auditor` → 200, `user` → 403
  `admin_forbidden`, битый/просроченный → 403, без кредов → 401.
  Legacy `X-Admin-Token` (service-token) и парольная форма (`ADMIN_PASSWORD`)
  сохранены как fallback. RBAC внутри views (auditor ≠ admin) — KI-045
  (закрыт в итерации 27: auditor read-only, admin — PromptAdmin edit).
- **Что:** смонтировать `starlette-admin` на `/admin` (AsyncEngine из
  `app.db.session`), добавить роль `admin` в `users` (enum `user/admin/
  auditor`, `password_hash`, миграция), аутентификацию админки через
  `AuthProvider`, views для `annotated_games` (RAG) и `commentaries`
  (просмотр, фильтры, мягкое удаление, восстановление), read-only views
  для `games`/`edits`. Все мутации — через сервисный слой, чтобы их видел
  аудит (T-035).
- **DoD:** `/admin` недоступен без аутентификации; `user` получает 403,
  `admin` видит и редактирует RAG/комментарии; мягкое удаление RAG-партии
  исключает её из retrieval; интеграционные тесты входа/ролей/CRUD;
  миграция `users.role`/`password_hash` с backfill `user`.
- **Оценка:** 3

### [x] T-029. Prompt Management (итерация 17)
- **Зависит от:** T-014, T-028
- **Статус итерации 17:** таблица `prompts` (`level`, `content`,
  `version`, `updated_at`), миграция `e3f4a5b6c7d8`, seed из `.txt` при
  старте, async `load_system_prompt` с in-memory кэшем и
  `reset_prompt_cache`, редактируемый `PromptAdmin` (только `content`;
  версия инкрементируется, кэш сбрасывается после commit). Тесты:
  `tests/integration/test_prompts_db.py`, `tests/unit/test_prompt_admin.py`.
  Остаток (история версий/rollback/API/аудит/preview) — T-029b.
- **Что:** вынести `app/prompts/{beginner,club,gm}.txt` в таблицу
  `prompts` с версиями и одной активной версией на `slug`; API
  `GET/PUT /v1/admin/prompts`, `POST /v1/admin/prompts/{slug}/rollback`;
  страница Prompt Editor в админке (редактор, diff, история, активация);
  горячая инвалидация кеша `load_system_prompt`; предпросмотр на тестовой
  партии. Few-shot-примеры — отдельными слагами/версиями; секреты — только
  ENV/Vault, не в `prompts`.
- **DoD:** правка активного промпта в админке меняет текст следующего
  LLM-вызова без рестарта; старые версии неизменяемы; rollback возвращает
  предыдущую; тесты версионирования и кеша; каждое изменение — в
  `audit_logs`.
- **Оценка:** 3

### [ ] T-029b. Prompt Registry: история, rollback, API, аудит
- **Зависит от:** T-029, T-035
- **Что:** неизменяемые версии `prompts` (новая строка на правку), diff
  и история в админке, `POST /v1/admin/prompts/{slug}/rollback`,
  `GET/PUT /v1/admin/prompts`, предпросмотр на тестовой партии; запись
  каждого изменения в `audit_logs`; мультипроцессная инвалидация кэша
  через Redis Pub/Sub + TTL.
- **DoD:** правка создаёт версию, старые версии неизменяемы, rollback
  возвращает предыдущую; изменения видны в аудите; тесты версий/кеша.
- **Оценка:** 2

### [x] T-030. LLMOps Observability (итерация 27, финал)
- **Зависит от:** T-013, T-025, T-028
- **Статус итерации 16:** OTel-спаны `llm_generate`/`llm_stream` в
  `OpenAICompatibleClient` с атрибутами `llm.model`, `llm.level`,
  `llm.tokens_in`, `llm.tokens_out`, `llm.cost_usd`; экспорт
  `console`/`otlp` при `OTEL_ENABLED=true`; тесты
  `tests/unit/test_observability.py`.
- **Статус итерации 27 (закрыто):** миграция `d8e9f0a1b2c3` — таблица
  `llm_calls` по DESIGN §4.4 (trace_id/span_id, actor/game/commentary,
  operation/provider/model/level, prompt_slug/version, токены/стоимость/
  латентность/cache_hit/status/error_code, индексы BRIN + DESC);
  `TraceMiddleware` (W3C traceparent, `X-Trace-Id`, contextvars,
  structlog; OTel `http_request` с корреляцией); recorder
  `app/llm/calls.py` пишет вызовы `complete`/`stream_generate`
  fire-and-forget (best-effort, без PII); контекст operation/actor_id/
  game_id в commentary/chat (sync+SSE); read-only view `LlmCallAdmin`.
  Остаток: warmup и commentary_id/cache_hit — KI-046, необработанные 500
  без `X-Trace-Id` — KI-047, retention `llm_calls` — KI-048 (закрыт в
  итерации 29; партиционирование — KI-052), дашборд — T-109 (итерация 29).
- **Что:** сквозной `trace_id` (W3C Trace Context) от HTTP-запроса до
  вызова LLM и валидатора; OTel-спаны `http_request`, `llm.complete`,
  `validator`, `rag.retrieve`; экспорт OTLP в коллектор (Jaeger/Tempo),
  опционально Langfuse; durable-таблица `llm_calls` (`trace_id`, `model`,
  `tokens_in/out`, `cost_usd`, `latency_ms`, `cache_hit`, `status`);
  дашборды p50/p95 latency, error rate, tokens/cost по уровням и моделям.
- **DoD:** 100% вызовов LLM имеют `trace_id` и запись в `llm_calls`;
  трейс виден в OTel-бэкенде; дашборд p95 и стоимости; sampling
  настраивается; тесты propagation и записи usage; PII в спаны не
  попадает. **Выполнено в объёме MVP** (дашборд — T-109, закрыт в
  итерации 29; sampling — отдельный шаг).
- **Оценка:** 4

### [x] T-030b. OTel-спаны validator/RAG (итерация 27)
- **Зависит от:** T-030
- **Что:** спаны `validator.validate` (moves/valid/illegal_count,
  Status OK/ERROR) и `rag.retrieve` (top_k/results, OK/ERROR),
  `trace.id` из `app.tracing.current_trace_id()` (fallback — OTel span);
  PII/тексты в атрибуты не пишутся.
- **DoD:** спаны видны в OTel-экспортере, correlation с `X-Trace-Id`,
  тесты `tests/unit/test_observability_validator_rag.py`; старые тесты
  зелёные.
- **Статус:** выполнено; level в этих сервисах не доступен (нет в
  сигнатурах) — отмечено в отчёте.

### [x] T-031. FinOps & Token Rate Limiting (итерация 17)
- **Зависит от:** T-009, T-013, T-030
- **Статус итерации 17:** дневные Redis-счётчики
  `usage:{date}:{ip|user}`, `TokenBudgetMiddleware` для
  analyze/commentary/chat (+stream), `429 token_budget_exceeded` с
  `X-RateLimit-Reset`/`Retry-After`; `daily_token_limit_free/pro`;
  стоимость `cost_usd` считается по ценам из конфига и пишется в
  `commentaries` + OTel-спан. Тесты: `tests/unit/test_token_budget.py`,
  `tests/integration/test_token_budget_api.py`. Остаток (durable
  `usage_daily`, месячные/пользовательские бюджеты, админ-настройки,
  алерты) — T-031b.
- **Что:** бюджеты не только по числу запросов, но и по токенам/стоимости
  на пользователя, IP и глобально (день/месяц); Redis-счётчики с
  атомарным `INCRBY`/Lua, durable-агрегаты `usage_daily`; настройки
  лимитов в `system_settings` и админке; ответ `429` с
  `X-Token-Budget-*`/`Retry-After`, различие «бюджет исчерпан» и
  «rate limit»; алерты при 80/100% бюджета.
- **DoD:** тесты: запрос сверх бюджета отклоняется, счётчики не
  превышают лимит при гонке, сброс периода корректен; стоимость считается
  по прайсу модели; админ может менять лимит; дашборд расходов.
- **Оценка:** 3

### [x] T-031b. FinOps: durable-агрегаты (итерация 28, MVP)
- **Зависит от:** T-031, T-035
- **Статус итерации 28:** миграция `e9f0a1b2c3d4` — таблица `usage_daily`
  (`subject_type/subject_key/day`, tokens_in/out, cost_usd, requests,
  UNIQUE(subject_type, subject_key, day), индексы день+тип и субъект+день);
  `record_token_usage` делает best-effort upsert (`ON CONFLICT DO UPDATE`,
  фоновая задача + отдельная сессия, `wait_for_pending_usage_upserts`);
  `/v1/users/me/usage` — Redis + DB fallback (`used = max(redis, db)`);
  `scripts/aggregate_usage.py` — месячные суммы, `--json`,
  `--retention-days` (90 по умолчанию), `--no-cleanup`. Тесты:
  `test_finops_durable.py` (20), `test_usage_daily.py` (5).
  Остаток: месячные лимиты/алерты 80/100%, reserve/reconcile,
  настройка лимитов в админке, дашборд расходов, планировщик агрегации
  (KI-049), партиционирование `llm_calls` (retention закрыт в итерации 29,
  партиционирование — KI-052).
- **Что (полная постановка):** `usage_daily` (upsert из Redis), лимиты на
  пользователя/IP/глобально и на месяц, reserve/reconcile по фактическим
  токенам, настройка лимитов в `system_settings`/админке, алерты 80/100%,
  дашборд расходов; резолвинг тира из JWT, а не из `X-User-Tier`
  (KI-031); точные `X-Token-Budget-*` в успешных ответах.
- **DoD (MVP):** счётчики переживают рестарт Redis; `/usage` отдаёт
  durable-данные; месячный скрипт и retention работают; тесты на upsert и
  fallback. Дашборд/алерты — T-109/T-031c.
- **Оценка:** 3

### [x] T-032. Semantic Cache (итерация 19)
- **Зависит от:** T-009, T-016, T-017, T-022
- **Статус итерации 19:** таблица `semantic_cache` (`fen_hash`,
  `fen_embedding vector(768)`, `level`, `prompt_hash`, `response_text`,
  `created_at`), миграция `a5b6c7d8e9f0`, HNSW-индекс; exact по
  `fen_hash+level+prompt_hash`, semantic по cosine distance < 0.05;
  кэш в `/v1/commentary`, `/chat` и SSE-ветках; TTL 30 дней + чистка
  при старте; `prompt_hash` включает sysprompt/модель/style/question.
  Тесты: `test_semantic_cache.py`, `test_cache_integration.py`; smoke:
  второй одинаковый комментарий — 0.030 с (< 100 мс).
  Остаток (Redis hot-слой, метрики hit/miss, engine-cache, `cache=off`)
  — T-032b.
- **Что:** кэш ответов LLM и результатов движка по нормализованному FEN:
  точный ключ `(fen_hash, level, prompt_slug+version, model)` +
  семантический поиск по эмбеддингу позиции в `semantic_cache`
  (pgvector); Redis как hot-слой, Postgres — durable; TTL и
  инвалидация при смене активного промпта/модели; метрики hit/miss и
  сэкономленных токенов; флаг `cache=off` для отладки.
- **DoD:** повторный идентичный запрос отдаётся из кэша без вызова LLM;
  семантически близкая позиция (cosine distance ≤ порога) переиспользует
  ответ только при совпадении уровня; нет утечки между уровнями; тесты
  hit/miss/инвалидации; hit rate виден в метриках.
- **Оценка:** 4

### [ ] T-032b. Semantic Cache: Redis hot-слой и метрики
- **Зависит от:** T-032
- **Что:** Redis hot-слой exact-ключа, метрики hit/miss и
  сэкономленных токенов, кэш результатов движка (engine), флаг
  `cache=off`, инвалидация по смене промпта/модели, hit rate в
  админке/OTel.
- **DoD:** повторный exact-запрос не ходит в Postgres при горячем
  Redis; метрики видны; engine-кэш не смешивается с LLM.
- **Оценка:** 2

### [x] T-033. Prompt Injection Guardrails (итерация 18)
- **Зависит от:** T-022, T-030
- **Статус итерации 18:** `app/security/guardrails.py` —
  NFKC-нормализация, удаление control/zero-width/bidi, лимит 2000,
  7 эвристик (override/leak/role/jailbreak/role-tags, RU+EN);
  `inspect_user_input` блокирует и возвращает `rule_ids`, в
  `/v1/chat` и `/v1/chat/stream` — 422 `prompt_injection` + запись в
  аудит; в системный промпт чата добавлен запрет раскрывать инструкции.
  Тесты: `tests/unit/test_guardrails.py`, `test_api_chat.py`.
  Остаток (классификатор, `<user_question>`, `style`, корпус 20 атак,
  метрики) — T-033b.
- **Что:** защита `/v1/chat` (и полей `style`/`message`): лимиты длины и
  кодировки, нормализация Unicode, явные разделители
  `<user_question>…</user_question>`, инструкция иерархии в системном
  промпте, эвристики (override-фразы, «ignore previous», утечка
  системного промпта), опциональный классификатор на локальной LLM,
  выходной фильтр; `injection_events` без хранения сырого текста;
  метрики block/flag; конфиг в `system_settings`.
- **DoD:** корпус ≥20 атакующих промптов: detection ≥95%, false positive
  ≤5% на легальных вопросах; блокировка возвращает 422 с кодом
  `prompt_injection`; системный промпт не утекает; события пишутся в
  аудит/метрики; тесты на корпусе.
- **Оценка:** 3

### [ ] T-033b. Guardrails: классификатор, корпус, output filter
- **Зависит от:** T-033, T-030
- **Что:** опциональный LLM-классификатор инъекций, обёртка
  `<user_question>…</user_question>`, защита поля `style` в
  `/v1/commentary`, выходной фильтр на утечку системного промпта,
  `injection_events` без сырого текста, метрики block/flag, корпус
  ≥20 атак + 50 легальных вопросов (detection ≥95%, FP ≤5%).
- **DoD:** все поверхности (chat/style/stream) защищены; корпус в CI;
  false positive ≤5%; события и метрики доступны.
- **Оценка:** 2

### [x] T-034b. Dataset Export в Admin UI (итерация 28)
- **Зависит от:** T-034, T-028
- **Статус итерации 28:** `GET {api_prefix}/admin/exports/edits.jsonl` —
  NDJSON-стрим (keyset-батчи), `Content-Disposition: attachment;
  filename="edits.jsonl"`; авторизация JWT admin/auditor (user → 403,
  без кредов → 401) **и** cookie-сессия `admin_session` (кнопка из
  админки); фильтры `date_from/date_to` (по `edits.created_at`),
  `level`, `human_edited`, `schema_version` (1|2, default 2);
  v2 переиспользует `scripts/export_dataset` (system-промпт + пакет
  фактов, пропуск no_game/invalid_pgn/no_analysis), v1 — legacy
  original→edited. Страница `/admin/exports/dataset`
  (`DatasetExportView`, меню «Экспорт датасета») с формой фильтров и
  кнопкой скачивания; доступна admin/auditor. Тесты:
  `test_admin_exports.py` (11). Остаток: checksum/размер, фоновая задача
  «>5 с»/подписанные ссылки — KI-050 (аудит `dataset.export` закрыт в
  итерации 29).
- **Статус итерации 31 (KI-050 закрыт):** `POST /v1/admin/exports/jobs`
  (202) → BackgroundTasks пишет NDJSON в `EXPORT_DIR` (по умолчанию
  `data/exports/`, вне web-root); таблица `export_jobs` (queued/running/
  done/failed, снимок фильтров, `rows_exported`, `file_size`, `sha256`);
  `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/download` (409 пока не
  готово, 410 если файл вне EXPORT_DIR); в админке — форма «Создать
  экспорт» с поллингом и кнопкой «Скачать». Создание — только
  admin/legacy (auditor read-only). Подписанные ссылки не входили в
  DoD; ограничение «джоба не переживает рестарт» — KI-053.
- **Статус итерации 32 (KI-053 закрыт):** выгрузка вынесена в
  arq-воркер (`app/worker.py`, корневой `worker.py`, `WorkerSettings`);
  API только `enqueue_job` (503 `export_queue_unavailable`, джоба
  `failed` при недоступном Redis); recovery `running>15 мин → failed`
  при старте API и воркера; docker-сервис `worker` в dev/prod compose
  с общим volume. Retention закрыт в итерации 33 (T-045/KI-054);
  подписанные ссылки — KI-057.
- **Статус итерации 35:** в `/admin` добавлен read-only view
  `export_jobs` (таблица статусов, rows/size/sha256/timestamps, фильтр
  по `status`); создание/правка через view запрещены (admin/auditor).
- **Что (полная постановка):** страница Export в админке и `GET
  /v1/admin/exports/edits.jsonl` (streaming): фильтры по дате, уровню,
  модели, `human_edited`; схема JSONL; опциональное вырезание PII;
  checksum, размер, `audit_logs`; лимит строк и фоновая задача для
  больших выгрузок.
- **Оценка:** 2

### [~] T-034. Dataset Export (частично, итерация 19)
- **Зависит от:** T-021b, T-028
- **Статус итерации 19:** `scripts/export_dataset.py` — streaming JSONL
  из `edits` (messages + метаданные, фильтры `--level`, `--limit`,
  `--min-id`). Остаток: админ-страница Export, подписанные ссылки,
  checksum/размер, аудит выгрузки, фоновая задача — не входит в
  DoD итерации 19.
- **Что:** страница Export в админке и `GET
  /v1/admin/exports/edits.jsonl` (streaming): фильтры по дате, уровню,
  модели, `human_edited`; схема JSONL (`id`, `commentary_id`,
  `editor_id`, `original_text`, `edited_text`, `level`, `model`,
  `created_at`, `schema_version`); опциональное вырезание PII; checksum,
  размер, `audit_logs`; лимит строк и фоновая задача для больших
  выгрузок.
- **DoD:** выгрузка 10k записей стримится без роста памяти; каждая строка
  валидна по JSON-схеме; доступ только `admin`/`auditor`; факт выгрузки
  в аудите; интеграционный тест.
- **Оценка:** 2

### [x] T-035. Security Audit (итерация 18)
- **Зависит от:** T-028
- **Статус итерации 18:** таблица `audit_logs` (`timestamp`, `actor`,
  `action`, `target_id`, `details`, `prev_hash`, `current_hash`),
  миграция `f4a5b6c7d8e9`, advisory-lock + hash-chain, триггер
  `UPDATE/DELETE → append-only`; аудит правки промпта, `PATCH
  /v1/commentaries/{id}` и заблокированной инъекции; read-only
  `AuditLogAdmin` и страница «Проверить целостность» с проверкой всей
  цепочки. Тесты: `tests/integration/test_audit.py`, `test_admin.py`,
  `test_api_chat.py`, `test_api_commentaries_patch.py`.
  Остаток (audit login/logout/бюджетов/экспорта, отдельные IP/UA,
  retention/SIEM, роль `auditor`) — T-035b.
- **Что:** append-only `audit_logs`: actor (id/hash, без FK), IP,
  user-agent, action, entity, before/after JSONB, `trace_id`,
  `request_id`; hash-chain (`prev_hash`/`self_hash`) и триггеры,
  запрещающие `UPDATE`/`DELETE`; аудит входа/выхода, CRUD RAG, правок
  промптов, бюджетов, экспорта и правок комментариев; ретенция и экспорт
  в SIEM; страница Audit Log с фильтрами.
- **DoD:** ни одна админ-мутация не проходит без записи; `UPDATE/DELETE`
  по `audit_logs` отклоняются БД; целостность hash-chain проверяется
  CLI/тестом; запись содержит актора, IP, действие и diff; страница
  Audit Log доступна роли `auditor`.
- **Оценка:** 3

### [ ] T-035b. Audit: полный охват, SIEM, retention
- **Зависит от:** T-035, T-028
- **Что:** отдельные `actor_ip`/`user_agent`/`request_id`/`trace_id`,
  аудит login/logout, бюджетов, экспорта и CRUD RAG; фильтры по
  actor/action/entity/date; CLI `python -m app.audit.verify`; retention
  и экспорт в SIEM; роль `auditor` (read-only); партиционирование.
- **DoD:** каждая админ-мутация и вход/выход в аудите; фильтры и
  экспорт; проверка цепочки из CLI; retention-политика.
- **Оценка:** 2

---

## Фаза 8. Frontend UX, LLM Evals и CI (итерации 20–23)

### [x] T-036. История партий и маршруты (итерация 20)
- **Что:** `GET /v1/games` (последние 20), `GET /v1/users/me/usage`,
  страницы `/history` и `/analysis/:id`, SPA-fallback в production.
- **Статус:** `/v1/games` парсит PGN (white/black/event) и отдаёт
  `level`; `SPAStaticFiles` отдаёт `index.html` для клиентских route;
  тесты `test_api_games.py`, `test_spa.py`.

### [x] T-037. Лимиты и история чата (итерация 20)
- **Что:** бейдж `Free: used/limit`, красный при превышении + модалка
  Pro; `UsageBadge` опрашивает `/v1/users/me/usage`; чат в Zustand с
  `sessionStorage`, история уходит в `/v1/chat` и `/v1/chat/stream`;
  backend повторно проверяет user-реплики guardrails.
- **Статус:** tier/usage берутся из того же IP+`PRO_IPS`, что и
  `TokenBudgetMiddleware`; история ограничена 20 репликами.

### [x] T-038. Mobile, skeleton, error boundary (итерация 21)
- **Что:** адаптивность до 480 px, skeleton-лоадеры анализа/истории,
  `ErrorBoundary` с кнопкой reload, `AnalysisWorkspace` без дублирования
  разметки.
- **Статус:** CSS media 1280/820/640/480; `Skeleton` с `role=status`;
  fallback для ошибок рендера.

### [x] T-039. LLM Evaluations в админке (итерация 22)
- **Что:** `llm_evaluations` (model, level, accuracy/usefulness/style),
  read-only view в `starlette-admin`, `evaluate_llm.py` пишет оценки в БД.
- **Статус:** миграция `b6c7d8e9f0a1`; `LlmEvaluationAdmin`; сохранение
  best-effort, отчёт MD/JSON остаётся.

### [x] T-040. CI caching (итерация 23)
- **Что:** pip-кэш, pnpm store cache, `workflow_dispatch`, полный набор
  `ruff`/`mypy`/`alembic`/`pytest`/`typecheck`/`build`/`test`.
- **Статус:** workflow обновлён; GitHub Actions не запускался локально.

### [x] T-041. JWT-аутентификация и изоляция данных (итерация 24, KI-035)
- **Зависит от:** T-028, T-019
- **Что:** миграция `users.role/is_active/password_hash/last_login_at`
  (`admin_role`: user/admin/auditor), `commentaries.user_id`,
  `edits.user_id`; `app/auth/` (passlib/bcrypt, python-jose access+refresh,
  `get_current_user`, `require_admin`); `/auth/register|login|refresh|me`;
  защита `/v1/analyze`, `/v1/games*`, `/v1/commentary*`, `/v1/chat*`
  фильтром по владельцу (admin видит все, чужой id → 404); usage по роли;
  фронтенд: `/login`, токены, refresh-интерцептор, guard, профиль в шапке.
- **DoD:** регистрация/вход; без токена 401; пользователь видит только
  свои партии; KI-035 закрыт; `ruff`/`mypy`/`pytest`/`pnpm build` зелёные.
- **Статус:** выполнено (итерация 24); ограничения — KI-036…038
  (bcrypt-pin, нет отзыва refresh/2FA, бюджеты пока по IP).

### [x] T-036b. E2E браузерных сценариев (итерация 25)
- **Что:** Playwright: загрузка PGN → анализ → комментарий; `/history` →
  `/analysis/:id`; mobile viewport; usage-badge.
- **DoD:** 5 сценариев в CI; скриншоты/видео при падении.
- **Статус:** выполнено (итерация 25): `@playwright/test` 1.64.1,
  `web/playwright.config.ts` (projects chromium + mobile), 5 spec в
  `web/e2e/`, скрипт `test:e2e`, CI-job `e2e` + `upload-artifact` при
  падении (trace/screenshot/video). Mocked-режим — 10 passed; real-backend
  (chromium) — 5 passed. Ограничение: в CI по умолчанию page.route-моки,
  реальный backend в CI не поднимается — KI-040.

### [x] T-042. Загрузка новых PGN в RAG (итерация 25)
- **Зависит от:** T-017 (RAG/ingest), T-104 (eval-набор)
- **Что:** прогнать `data/sample3.pgn`, `data/sample4.pgn`,
  `data/sample5.pgn` через `scripts/ingest_pgn.py --limit 1000
  --source new_samples`; вывести количество записей в `annotated_games`;
  включить новые партии в eval-набор.
- **DoD:** 522 новые партии загружены (500 + 13 + 9), итого **624**
  записи (102 baseline + 522), 621 с аннотациями, 0 `NULL` embedding;
  eval может фильтровать новые партии.
- **Статус:** выполнено: ingest exit 0 для трёх файлов; в
  `scripts/evaluate_llm.py` добавлен `--source` (фиксирует eval-набор),
  методика — в `docs/development/reports/LLM_EVAL.md`. Ограничение
  окружения: рабочая БД с baseline 102 недоступна (порт 5434 занят),
  данные загружены в temp Postgres 5435 (baseline воспроизведён
  рецептом итерации 12).

### [x] T-043. Auth tech debt: Argon2id, HttpOnly cookie, per-user FinOps (итерация 26)
- **Зависит от:** T-041
- **Что:** KI-036 — убрать passlib, Argon2id + миграция legacy bcrypt при
  логине; KI-037 — refresh в HttpOnly-cookie (`SameSite=Lax`, `Path=/auth`),
  `/auth/logout` + Redis-denylist, отзыв при ротации, фронтенд без
  `localStorage` (`credentials: include`, single-flight refresh, bootstrap
  через `/auth/refresh`); KI-038 — FinOps по `user:{id}`/tier из JWT.
- **DoD:** login не отдаёт refresh в JSON; logout → reuse 401; bcrypt-хеш
  при логине становится `$argon2id$`; лимиты по user_id; фронтенд-тесты и
  E2E зелёные.
- **Статус:** выполнено (итерация 26); остатки — KI-041, KI-042
  (KI-043 закрыт в итерации 27).

### [x] T-028b. Auth finalization: email verification, reset, logout-all (KI-041, итерация 32)
- **Зависит от:** T-028, T-041
- **Что:** `users.is_verified` (миграция `dcf1da1421cd`), mock
  email-sender (`app/email/sender.py`), `GET /auth/verify-email`,
  `POST /auth/forgot-password` / `reset-password`, глобальный отзыв
  refresh (`POST /auth/logout-all`, `revoked_before` в Redis, `iat_ms`);
  `/v1/analyze` и `/v1/commentary` → 403 `email_not_verified` для
  неподтверждённых (admin/auditor bypass).
- **DoD:** регистрация → письмо; без верификации API закрыт; reset меняет
  хеш и отзывает старые refresh; logout-all инвалидирует старые сессии;
  тесты `tests/integration/test_auth_ki041.py` (8) и
  `tests/unit/test_email_sender.py` (5).
- **Остаток:** frontend-страницы verify/reset и logout-all закрыты в
  итерации 33 (T-045/KI-055); 2FA/SSO, лимит попыток по email, SMTP,
  resend-verification — KI-056.

### [x] T-045. Frontend Auth UX & Export Retention (KI-055, KI-054, итерация 33)
- **Зависит от:** T-028b, T-034b
- **Что:** публичные страницы `/verify-email` (GET с токеном из URL),
  `/forgot-password` (нейтральный 202), `/reset-password` (новый пароль +
  редирект на `/login`); экран «Подтвердите email» в `AuthGate` для
  `is_verified=false` (admin/auditor bypass) и понятная ошибка 403
  `email_not_verified` в `HomePage`; меню профиля с «Выйти со всех
  устройств» (`POST /auth/logout-all`); i18n RU/EN; ссылки в письмах
  ведут на SPA (`APP_BASE_URL`), а не на JSON-API. Retention:
  `scripts/prune_export_jobs.py` (файлы внутри `EXPORT_DIR` + строки
  `export_jobs`, батчи, dry-run, json), `EXPORT_RETENTION_DAYS=7`, cron.
- **DoD:** Vitest 53, E2E mocked 28 (14×2), backend 761 (full);
  retention unit+integration 15; ruff/mypy/build green.
- **Остаток:** SMTP/2FA/SSO/лимит по email/resend — KI-056; signed URL
  экспортов — KI-057.
- **Статус итерации 35:** `/history` для неподтверждённого пользователя
  показывает плейсхолдер «Подтвердите email, чтобы увидеть историю»
  (вместо пустого списка); i18n RU/EN.

### [x] T-046. Release Prep: Security, GHCR, EN localization, Grafana alerts (T-110, T-027, T-106, T-113, итерация 34)
- **Зависит от:** T-027, T-028b, T-045, T-109b
- **Что:** T-110 — pip-audit и миграция python-jose→PyJWT, валидаторы
  prod-секретов/CORS, ревизия raw SQL; T-027 — `publish-docker` в CI
  (GHCR, теги `v*`/`:latest`, `VITE_API_BASE`); T-106 — `*_en.txt`,
  `DEFAULT_LANGUAGE`, `language`/`Accept-Language` в commentary,
  frontend `i18n.language`; T-113 — Grafana provisioning трёх alert
  rules.
- **DoD:** pip-audit без уязвимостей; prod-валидатор; E2E/back-end
  зелёные; `language=en` возвращает английский текст (тест); в Grafana
  видны 3 правила.
- **Статус:** выполнено; фактический push GHCR — при создании тега
  мейнтейнером.

### [x] T-044. Dataset Schema v2: fact_sheet → commentary (KI-039, итерация 26)
- **Зависит от:** T-104
- **Что:** `export_dataset.py` по умолчанию `schema_version=2`:
  `system` — промпт уровня, `user` — `build_fact_package(...)` из
  `games/moves/motifs`, `assistant` — `edited_text`; `metadata`
  (id/commentary_id/game_id/editor_id/level/model/created_at/
  fact_sheet_chars); v1 сохранён флагом. `train_lora.py` поддерживает
  `{1,2}`, печатает распределение и предупреждает про rewrite-v1.
- **DoD:** v2 экспортируется и читается dry-run; интеграционный тест
  seed→export→валидация; `LORA_TRAINING.md` обновлён.
- **Статус:** выполнено (итерация 26); обучение на GPU по-прежнему не
  выполнялось (KI-030 Open), N+1 экспорта — KI-044.

### [ ] T-039b. LLM evals: графики и фильтры
- **Что:** фильтры по модели/уровню/дате, средние по шкалам, график
  динамики в админке; экспорт CSV.
- **DoD:** dashboard с 3 графиками; фильтры сохраняются в query.

### [x] T-070. Интерактивная доска разбора (итерация 37)
- **Зависит от:** T-020, T-036b
- **Что:** `react-chessboard` 5 с `allowDragging`/`onPieceDrop`: легальный
  ход пользователя создаёт локальный вариант (`lib/variant.ts`:
  `applyUserMove`, `formatVariantLine`), строка «Вариант: 3. Ng5 Nd4» над
  доской, кнопка «Сбросить на партию»; нелегальный ход откатывается;
  вариант сбрасывается при навигации и смене анализа.
- **DoD:** доска интерактивна, вариант отображается и сбрасывается;
  unit-тесты хелперов + E2E с реальным перетаскиванием мыши (dnd-kit).
- **Статус:** выполнено (итерация 37); сохранение вариантов — не
  требовалось.

### [x] T-071. Загрузка PGN из файла (итерация 37)
- **Зависит от:** T-020
- **Что:** кнопка «Загрузить файл» (📎) рядом с PGN-полем: скрытый
  `input[type=file]`, `FileReader.readAsText`, лимит 1 МБ, проверка
  расширения `.pgn` (`lib/pgnFile.ts`), ошибки в баннере (i18n RU/EN).
- **DoD:** `.pgn` подставляется в редактор и анализируется; 1 МБ+1 и
  `.txt` отклоняются с понятной ошибкой; unit-тесты + E2E
  (`setInputFiles`).
- **Статус:** выполнено (итерация 37); MIME-снифинг содержимого — не
  входил в объём.

### [x] T-072. Контекст выбранного хода в чате (итерация 38)
- **Зависит от:** T-022, T-070
- **Что:** `selected_ply` в `POST /v1/chat` и `/v1/chat/stream`
  (`ChatRequest.selected_ply: int | None`, `ge=0`); бэкенд добавляет в
  пользовательский промпт строку «Пользователь сейчас смотрит на ход
  13... Re8 (…оценка +0.40)…», поэтому вопросы без номера хода («Почему
  это плохо?») относятся к выбранному полуходу; строка входит в
  `prompt_hash`; `selected_ply` в SSE `meta`; фронтенд шлёт его только
  при `> 0`, над чатом — «Контекст: 13... Re8».
- **DoD:** выбрать ход 5 → спросить «Почему это плохо?» → в промпте
  контекст хода 5; None/вне анализа — без строки; unit/integration/E2E
  (`chat-context`) зелёные.
- **Статус:** выполнено (итерация 38); для `0` отдаётся строка о
  стартовой позиции (фронтенд 0 не отправляет).

### [x] T-073. Стрелки на доске для рекомендованных ходов (итерация 38)
- **Зависит от:** T-070
- **Что:** `lib/arrows.ts` (`extractSanMoves`, `buildArrows`): зелёная
  стрелка рекомендации выбранного полухода + стрелки для SAN-ходов из
  текста комментария, резолвленных на текущей позиции (`chess.js`);
  красная — для `blunder`/`mistake`/`inaccuracy`; `Board` принимает
  `arrows` (react-chessboard 5); текст комментария — в
  `analysisStore.commentaryText`.
- **DoD:** стрелки появляются после упоминания хода в комментарии и для
  рекомендации; корректные цвета; unit-тесты + E2E (`board-arrows`).
- **Статус:** выполнено (итерация 38); стрелки скрыты в режиме варианта
  и не перерисовываются на каждом SSE-дельте.

## Backlog (после MVP)

- **T-101.** Leela Chess Zero как второй движок для гроссмейстерского
  уровня.
- **T-102.** Lichess Studies / открытые базы как источник RAG.
- **T-103.** Лицензия ChessBase Mega Database 2026.
- **[x] T-104.** LoRA-пайплайн на `edits` (итерация 25, schema v2 —
  итерация 26): `scripts/export_dataset.py` (схема `system → user →
  assistant`; с итерации 26 по умолчанию `schema_version=2` —
  `user` = пакет фактов `build_fact_package`, `assistant` = `edited_text`),
  `scripts/train_lora.py` (реальный SFT на peft/transformers: QLoRA 4-bit,
  r=16, alpha=32, q_proj/v_proj, 3 эпохи, lr=2e-4, маскирование лосса на
  assistant; `--dry-run`, `--check-env`, поддержка schema {1,2}, понятные
  ошибки без GPU), `LORA_ADAPTER_PATH` в конфиге/фабрике (адаптер
  учитывается только если путь существует),
  `docs/development/reports/LORA_TRAINING.md` (GPU RunPod/Colab,
  стоимость, vLLM `--lora-modules`, оценка).
  **Реальное обучение не выполнялось** — на машине нет GPU/CUDA
  (KI-030 остаётся Open).
- **T-105.** On-prem профиль без облачных LLM (`LLM_PROVIDER=local`).
- **T-106.** Английская локализация комментариев. **[x]** — выполнено в
  T-046 (итерация 34): `*_en.txt`, `DEFAULT_LANGUAGE`, `language`/
  `Accept-Language`, frontend передаёт `i18n.language`.
  **Итерация 35:** локализация расширена на PDF (`language` query-param,
  английские подписи), чат (`chat_en.txt`) и ошибки API
  (`Accept-Language`); KI-058 закрыт. Остатки — русский факт-пакет,
  en-промпты файлами (не в админке), нет вычитки носителем.
### [x] T-107. Экспорт комментария в PDF (итерация 30)
- **Зависит от:** T-016, T-019
- **Статус итерации 30:** `GET /v1/commentaries/{id}/export/pdf` —
  серверный PDF (fpdf2 + TTF-шрифт с кириллицей): шапка (игроки, турнир,
  дата, результат, уровень, модель, статус), PGN-ходы, текст комментария
  с абзацами, номер страницы; `Content-Disposition: attachment`;
  JWT + изоляция данных (чужая запись → 404), `asyncio.to_thread` для
  CPU-bound генерации; битый PGN не ломает экспорт. Кнопка «Скачать PDF»
  в панели комментария (RU/EN), API-хелпер с refresh. Тесты:
  `tests/unit/test_pdf_export.py`,
  `tests/integration/test_commentary_pdf.py`, `web/src/api.pdf.test.ts`.
  DOCX остаётся на будущее.
  **Итерация 35:** `GET .../export/pdf` принимает `language` (`ru`/`en`,
  по умолчанию `DEFAULT_LANGUAGE`); при `en` — английские заголовки и
  подписи секций.
### [x] T-107b. Диаграмма доски в PDF (итерация 31)
- **Зависит от:** T-107
- **Статус итерации 31:** `app/export/board.py` — `chess.svg.board()` →
  PNG через `cairosvg`; `select_diagrams` выбирает финальную позицию и
  до двух критических моментов (`blunder`/`mistake` по `|delta_cp|`) с
  подсветкой последнего хода. PDF вставляет PNG через `fpdf2` (82 мм,
  подпись), pypdf видит image-XObject. При отсутствии
  `cairosvg`/`libcairo2` — graceful degradation: PDF без картинки, не
  500 (`render_board_png → None`). В `Dockerfile`/CI добавлен
  `libcairo2`; dependency `cairosvg>=2.7`. Тесты:
  `tests/unit/test_pdf_board.py` (9), интеграционный контракт
  доступности рендерера в `test_commentary_pdf.py`.
### [x] T-108. Кэш результатов движка по `fen_hash` (итерация 30)
- **Зависит от:** T-008, T-030
- **Статус итерации 30:** миграция `19bf1fdc2a73` — таблица
  `engine_cache` (`fen_hash`, `engine_version`, `depth`, `multipv`,
  `lines` JSONB, `created_at`, `UNIQUE(fen_hash, engine_version, depth,
  multipv)`, индекс `created_at`); `app/engine/cache.py`
  (`EngineCache.lookup/store/cleanup_expired`), интеграция в
  `EngineService.analyze` (проверка до поиска, запись после, версия из
  UCI `id name`), `ENGINE_CACHE_ENABLED`/`ENGINE_CACHE_TTL_DAYS` (30),
  чистка при старте; выключение флагом. Тесты: `tests/unit/test_engine_cache.py`,
  `tests/integration/test_engine_cache.py` (fake UCI + реальный Stockfish:
  повторный `/v1/analyze` быстрее). Исходный backlog-пункт «кэш retrieval
  по `fen_hash`» (`retrieval_cache`) остаётся не включён — это часть T-018
  / T-032b, не engine cache.
### [x] T-109. LLMOps-дашборд в админке (итерация 29)
- **Зависит от:** T-030, T-031b, T-028
- **Статус итерации 29:** `/admin/llmops` (`LlmOpsDashboardView`,
  `app/llmops_dashboard.py`, Jinja2 + Bootstrap) — сводка за 24 часа, 7 и
  30 дней: latency p50/p95/p99 (без cache-hit), error rate
  (`error`/`timeout`), tokens/cost с разбивкой по `model`/`level`,
  cache-hit rate; виджет «Топ-5 пользователей по токенам» из
  `usage_daily`. Read-only, admin/auditor; тесты
  `tests/integration/test_admin_llmops_dashboard.py`,
  `tests/unit/test_llmops_dashboard_unit.py`. Retention `llm_calls` —
  `scripts/prune_llm_calls.py` (KI-048 закрыт); партиционирование —
  KI-052.
- **Что (исходная постановка):** OpenTelemetry-дашборды p50/p95 latency,
  error rate, tokens/cost в Grafana/Tempo по `llm_calls`/спанам.
  Grafana/Tempo-дашборды и алерты аномалий остаются отдельным шагом
  (T-113); в итерации 29 визуализация сделана внутри админки без
  внешнего OTel-бэкенда.
- **Статус итерации 31 (T-109b):** полноценный стек
  `docker-compose.observability.yml` — `otel-collector` (OTLP gRPC/HTTP,
  spanmetrics-коннектор), `tempo`, `prometheus`, `grafana`.
  `app/observability.py` при `OTEL_ENABLED=true` экспортирует спаны и
  OTLP-метрики (`llm.calls/tokens/cost/latency`) в коллектор; HTTP-спан
  `http_request` получает `http.route`/`http.response.status_code` для
  spanmetrics. Дашборд `deploy/grafana/dashboards/chessannotator-
  observability.json` (p95/p99 для `/v1/commentary` и `/v1/analyze`,
  5xx error rate, tokens/cost) провижинится автоматически; datasource
  Tempo — для трейсов. Алерты/Alertmanager по-прежнему T-113.
- **T-110.** Pen-test и security review. **[x]** — выполнено в T-046
  (итерация 34): pip-audit (python-jose/ecdsa заменены на PyJWT),
  CORS/secrets-валидаторы, ревизия raw SQL.
- **T-111.** Multi-tenant организации и RBAC (роли `owner/admin/editor/
  viewer`, изоляция данных по `org_id`).
- **T-112.** A/B-эксперименты версий промптов с автоматической оценкой
  LLM-as-judge и выбором победителя.
- **T-113.** Алерты аномалий стоимости (Grafana/Alertmanager): всплеск
  токенов, дорогая модель, рост error rate. **[x]** — выполнено в T-046
  (итерация 34): 3 провижиненных правила Grafana (HighErrorRate,
  HighLatency, LLMBudgetExceeded).
- **T-114.** PII-редакция в трейсах, аудите и экспортах (email, IP,
  имена) с настраиваемыми правилами.
- **T-115.** Backup/DR: PITR для Postgres, версии промптов, RAG-датасет
  и audit-log в object storage; регулярные restore-учения.
- **T-116.** Kubernetes/Helm-манифесты с HPA для API и пула движков.
- **T-117.** Canary/blue-green деплой и автоматический rollback по
  метрикам error rate/latency.
- **T-118.** SSO/SAML для админки (OIDC-провайдер, SCIM-провижининг).
- **T-119.** Юридическое хранение и legal hold для audit/edits
  (retention-политики по регионам).

---

## Как обновлять этот файл

- В конце каждой итерации: отметить `[x]` выполненные задачи,
  добавить отчёт в `docs/development/RELEASES.md`.
- Если задача оказалась больше, чем думали — разбить на подзадачи
  с новыми ID (например, `T-016a`, `T-016b`) и обновить зависимости.
- Если задача заблокирована — завести `KI-NNN` в `KNOWN_ISSUES.md`
  и пометить `[~]` с ссылкой.
- Никогда не удалять задачи — только помечать `[x]` или переносить в
  Backlog с новым ID.