# ChessAnnotator AI Commentary Engine

[![CI](https://github.com/iilmchat/ai-chess-annotator/actions/workflows/ci.yml/badge.svg)](https://github.com/iilmchat/ai-chess-annotator/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.2.0-blue.svg)](docs/CHANGELOG.md)
[![Backend tests](https://img.shields.io/badge/backend_tests-1000_passed-brightgreen.svg)](docs/development/RELEASES.md)
[![Frontend tests](https://img.shields.io/badge/frontend_tests-129_passed-brightgreen.svg)](docs/development/RELEASES.md)
[![License](https://img.shields.io/badge/license-proprietary-lightgrey.svg)](#лицензия)

> **Ассистент-комментатор шахматных партий.** Загрузите PGN — получите
> разбор уровня гроссмейстера, адаптированный под ваш уровень: новичок,
> клубный игрок или гроссмейстер.

**Версия:** `v1.2.0` · **Стек:** Python 3.12 + FastAPI · React 19 · PostgreSQL 17 + pgvector ·
Redis · Stockfish 17 · LM Studio / DeepSeek · `arq` · OpenTelemetry/Grafana ·
Docker Compose
**© 2026 RuChating (iilmchat)**

---

## Что это

ChessAnnotator — веб-приложение с API, которое принимает шахматную партию
(PGN/FEN) и возвращает **связный комментарий гроссмейстерского качества**.
Архитектура гибридная: движок (Stockfish) и база
аннотированных партий дают проверенные факты, LLM превращает их в текст,
а валидатор прогоняет все варианты обратно через движок — так что ни один
вариант не попадает пользователю без проверки.

Проект вырос из идеи «LLM пишет комментарии» в **трёхуровневую систему**:
каждый уровень (новичок / клуб / гроссмейстер) имеет свою глубину анализа,
свои источники данных и свою политику выбора рекомендуемого хода.

В **v1.0.0** это полноценный сервис: JWT-аккаунты с email-верификацией и
сбросом пароля, админка с LLMOps-дашбордом и управлением промптами,
фоновые выгрузки через `arq`-воркер, PDF-экспорт с диаграммами доски,
локализация RU/EN (комментарий, чат, PDF, ошибки API) и
observability-стек Grafana/Tempo/Prometheus.

---

## Возможности

### 🎯 Три уровня ассистента

- **Новичок** — простой язык, базовые принципы (развитие, центр,
  безопасность короля), акцент на типичных ошибках. Дешёвая локальная LLM.
- **Клубный игрок** — баланс тактики и стратегии, объяснение альтернатив,
  статистика из базы похожих партий. Локальная средняя LLM.
- **Гроссмейстер** — профессиональная терминология, тонкие нюансы,
  исторический контекст варианта. Облачная мощная LLM + RAG.

Выбор уровня влияет на глубину движка, модель LLM, набор источников RAG и
системный промпт — не только на «сложность слов».

### ♟️ Анализ партии

- **Ingestion**: PGN, FEN, ручной ввод; проверка легальности, ECO-код,
  детект транспозиций, разметка фаз (дебют / миттельшпиль / эндшпиль).
- **Engine Service**: Stockfish 17, MultiPV, классификация ходов
  (blunder / mistake / inaccuracy / good / best), поиск критических
  моментов, пул из 4 процессов и кэш `engine_cache`.
- **Motif Detector**: слабые поля, отсталые и проходные пешки, открытые
  линии и диагонали, безопасность короля, тактические темы (связка,
  вилка, отвлечение, промежуточный шах).
- **Policy Layer**: детерминированный выбор рекомендуемого хода под
  уровень пользователя (не просто top-1 движка).
- **Валидатор**: каждый вариант из LLM-ответа прогоняется через движок;
  нелегальные/ошибочные отбрасываются, LLM переписывает.
- **Семантическая валидация (KI-023)**: помимо ходов проверяются
  утверждения о позиции — «слон на e6», «связка пешки f7» (RU/EN);
  неверные факты ведут к retry, а на последней попытке предложение
  вырезается вместо ошибки 502.

### 🖱️ Интерактивный разбор

- **Доска**: фигуры можно перетаскивать — легальный ход открывает
  вариант («Вариант: 3. Ng5 Nd4»), кнопка «Сбросить на партию»
  возвращает позицию из базы (T-070).
- **Загрузка PGN**: кнопка «Загрузить файл» (📎) читает `.pgn` до 1 МБ
  через `FileReader` и подставляет текст в поле (T-071).
- **Контекст чата (T-072)**: выбранный полуход передаётся в `/v1/chat` как
  `selected_ply`; вопрос «Почему это плохо?» без номера хода относится к
  ходу, который сейчас на доске; над чатом — строка «Контекст: 13... Re8».
- **Стрелки на доске (T-073)**: рекомендованный ход и ходы, упомянутые в
  комментарии, рисуются стрелками (зелёная — хороший ход, красная —
  ошибка); SAN извлекается из текста и проверяется на текущей позиции.

### 💬 Комментарий, чат и PDF

- **Commentary Generation**: LLM получает структурированный «пакет
  фактов» (оценки, варианты, мотивы, retrieved-аннотации) и пишет текст
  в стиле гроссмейстерских аннотаций.
- **Язык**: `language` (`ru`/`en`) в `/v1/commentary` и `/v1/chat`
  (fallback — `Accept-Language` → `DEFAULT_LANGUAGE`); английские
  промпты — `*_en.txt`, английский чат — `app/prompts/chat_en.txt`.
- **Чат по партии**: уточняющие вопросы («почему не работает этот ход?»,
  «что было бы, если...») с той же валидацией вариантов и guardrails.
- **SSE-стриминг**: `/v1/commentary/stream` и `/v1/chat/stream` —
  события `meta`/`delta`/`done`/`error`, текст печатается постепенно.
- **PDF-экспорт**: `GET /v1/commentaries/{id}/export/pdf` — шапка,
  ходы, комментарий, диаграммы доски (финальная позиция + критические
  моменты); `language` (`ru`/`en`) меняет подписи; кнопка «Скачать PDF»
  в UI.
- **Human-in-the-loop**: веб-редактор правок; правки идут в датасет для
  дообучения и few-shot.

### 🔐 Аккаунты, роли и безопасность

- **Аутентификация**: JWT (PyJWT, HS256), access 15 минут, refresh 7 дней
  в HttpOnly-cookie, Redis-denylist, ротация; пароли — Argon2id.
- **Email-верификация и восстановление**: mock-письмо, `/auth/verify-email`,
  `/auth/forgot-password`, `/auth/reset-password`, `/auth/logout-all`;
  генерация закрыта для неподтверждённых (403 `email_not_verified`).
- **Профиль (T-082)**: страница `/profile` — отображаемое имя и
  предпочитаемый уровень (`PATCH /auth/me`), смена пароля с проверкой
  текущего (`POST /auth/change-password`); имя хранится в
  `users.display_name` (миграция `a9f1f28d4c45`).
- **Реальный SMTP (T-082)**: `SMTPEmailSender` на `aiosmtplib` включается
  `EMAIL_SENDER=smtp` + `SMTP_HOST/PORT/USER/PASSWORD/FROM`; по умолчанию
  остаётся mock (ссылка в structlog для dev).
- **Подписка Pro и Stripe (T-083/T-084)**: Free 50k / Pro 500k токенов в
  день; кнопка «Оформить подписку» в модалке лимита ведёт в Stripe
  Checkout, `/billing/success|cancel` — возврат, `/profile` показывает
  тариф и открывает Stripe Customer Portal; webhook с проверкой подписи
  переключает `users.tier` (`checkout.session.completed` → pro,
  `customer.subscription.deleted` → free).
- **Роли**: `user` / `admin` / `auditor`; изоляция партий по `games.user_id`;
  админка с RBAC (auditor строго read-only).
- **Приватность**: партии не уходят во внешние LLM при уровне «новичок»
  (локальная модель); для «гроссмейстера» — опция «только локально».
- **Guardrails**: нормализация, 7 RU/EN-эвристик, `422 prompt_injection`;
  append-only аудит с hash-chain.
- **Rate limiting**: Redis-счётчики (login и общий API), дневной
  token-бюджет по тарифу из `users.tier` (free 50k / pro 500k).

### 📚 RAG, кэши и качество текста

- **Источники**: открытые аннотированные партии (Lichess Studies и др.),
  собственная растущая база через модуль загрузки; ChessBase Mega Database
  2026 — при наличии лицензии (T-103).
- **Хранилище**: PostgreSQL 17 + `pgvector`, эмбеддинги 768 (LM Studio
  nomic-embed).
- **Retrieval**: top-k похожих позиций + их аннотации → в контекст LLM
  как «гроссмейстерский контекст» (уровень `gm`).
- **Semantic cache**: exact FEN-ключ + vector-поиск, TTL 30 дней;
  второй запрос — миллисекунды.
- **LLM-evals**: `llm_evaluations` и `scripts/evaluate_llm.py`
  (LLM-as-judge); LoRA-пайплайн `scripts/export_dataset.py` +
  `train_lora.py` (3B/7B-адаптеры обучены и оценены в итерации 48:
  2.87 и 3.33/10, отклонены; KI-030 Closed, KI-060 — языковой дрейф 7B).
- **GPU-перенос (T-080)**: `.gitignore` закрывает веса (`models/`,
  `*.safetensors`), `scripts/generate_lora_dataset_from_db.py` собирает
  датасет v2 из БД (по умолчанию 500 партий), пошаговая инструкция —
  [GPU_TRAINING_GUIDE.md](docs/development/GPU_TRAINING_GUIDE.md).

### 🛠 Админка, LLMOps и фоновые выгрузки

- **Admin Panel** (`/admin`, starlette-admin): read-only views
  `annotated_games`, `commentaries`, `edits`, `audit_logs`, `llm_calls`,
  `llm_evaluations`; редактируемый `PromptAdmin`; импорт закрыт.
- **LLMOps-дашборд** `/admin/llmops`: p50/p95/p99, error rate,
  cache-hit rate, токены/стоимость, топ-5 пользователей.
- **Фоновые выгрузки**: API ставит задачу в `arq`-очередь, отдельный
  worker пишет NDJSON в `EXPORT_DIR`; `export_jobs` + статус/скачивание,
  read-only view в админке с фильтром по статусу, retention-скрипт.
- **Prompts**: промпты в БД (версия, hot-reload), seed из `*.txt`,
  инвалидация кэша между воркерами через Redis Pub/Sub.

### 📈 Наблюдаемость

- **OTel**: спаны `http_request`, `llm_*`, `validator.validate`,
  `rag.retrieve`; OTLP-метрики `llm.calls/tokens/cost/latency`;
  сквозной `trace_id` и заголовок `X-Trace-Id`.
- **Стек**: `docker-compose.observability.yml` — OTel Collector →
  Prometheus + Tempo → Grafana (дашборд p95/p99, 5xx, tokens/cost),
  alert rules `HighErrorRate` / `HighLatency` / `LLMBudgetExceeded`.
- **Durable-история**: `llm_calls` (провайдер, модель, уровень, токены,
  стоимость, latency, cache_hit, prompt_slug/version) и FinOps
  `usage_daily`.

### 🗄️ Данные и хранение

- **PostgreSQL 17 + pgvector**: 16 таблиц — партии, ходы, мотивы,
  аннотации, комментарии, правки, промпты, аудит, semantic cache,
  `llm_calls`, `usage_daily`, `engine_cache`, `export_jobs`.
- **Локальная ФС**: `EXPORT_DIR` (`data/exports/`, вне web-root) для
  готовых выгрузок и PDF; retention — `scripts/prune_export_jobs.py`.
- **S3-совместимое хранилище** (MinIO/R2 и т.п.) — в Backlog, не в v1.0.0.

### 🌍 Локализация

- **UI** — RU/EN (`react-i18next`); **комментарий, чат, PDF-экспорт,
  ошибки API** — RU/EN (`language`/`Accept-Language`, `DEFAULT_LANGUAGE`).
- Английские промпты лежат файлами (не редактируются в админке),
  факт-пакет остаётся русским — остатки зафиксированы в закрытом KI-058.

### 📱 Установка (PWA)

- **Манифест + Service Worker** (`vite-plugin-pwa`): приложение
  устанавливается из Chrome/Edge («Установить приложение»), открывается в
  отдельном окне (`display: standalone`), иконки 192/512 и maskable,
  `theme-color`, iOS-мета-теги (`apple-mobile-web-app-capable`).
- **Кэш — только статика**: workbox precache JS/CSS/HTML/иконок;
  `/v1/*`, `/auth/*`, `/admin`, `/health` исключены из навигационного
  fallback и не кэшируются (устаревшие данные/сессии недопустимы).

### 🧪 Качество и релиз

- **Тесты**: pytest (backend), Vitest (frontend), Playwright E2E
  (mocked-API и real-backend режим).
- **CI**: ruff, mypy, alembic (upgrade/check/roundtrip), pytest,
  typecheck, build, test, E2E, docker build + smoke, `publish-docker`
  по тегу `v*` → GHCR.
- **Слепой тест уровней**: `scripts/generate_blind_test.py` готовит
  3 партии × 3 уровня со слотами A/B/C, бланк и ключ организатора;
  сам тест с людьми — вручную (T-024, не проведён).

---

## Стек

| Слой | Технологии |
|---|---|
| **Backend** | Python 3.12, FastAPI, `python-chess`, собственный async UCI-клиент Stockfish, Pydantic, structlog |
| **Frontend** | React 19, Vite 6, TypeScript, `react-chessboard` 5.12.1, `chess.js`, `react-i18next` (RU/EN), Zustand |
| **Данные** | PostgreSQL 17 + **pgvector**, SQLAlchemy 2 + Alembic |
| **Очереди** | `arq` (фоновые выгрузки), Redis 7 (кэш, rate limit, denylist, очередь) |
| **Движки** | Stockfish 17 (LC0 — Backlog T-101) |
| **AI / LLM** | Локально: LM Studio + Qwen3-4B. Облако: DeepSeek |
| **RAG** | `pgvector`, LM Studio `text-embedding-nomic-embed-text-v1.5` (768, L2); hash — fallback тестов |
| **Экспорт** | `fpdf2` + TTF (PDF), NDJSON/JSONL (датасет) |
| **Хранилище** | локальная ФС `EXPORT_DIR` (S3 — Backlog) |
| **Инфраструктура** | Docker Compose (dev/prod/observability), OpenTelemetry → OTel Collector → Prometheus + Tempo → Grafana, SSE-стриминг |
| **Качество** | pytest, Vitest 2, Playwright, `ruff`, `mypy` |

---

## Быстрый старт

**Требования:** Python 3.12+, Node.js 22+, pnpm 12+, Docker Desktop,
Stockfish 17 (локальный бинарник; UCI-over-TCP не поддержан — KI-013).
На Windows/Linux/macOS x86_64 его ставит `python scripts/install_stockfish.py`.

### Dev: backend на хосте, инфраструктура в Docker

```bash
# 1. Клонировать репозиторий
git clone https://github.com/iilmchat/ai-chess-annotator.git
cd ai-chess-annotator

# 2. Поднять PostgreSQL 17 + pgvector и Redis
docker compose up -d postgres redis

# 3. Установить зависимости, окружение, Stockfish и миграции
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env
python scripts/install_stockfish.py   # Stockfish 17.1 → bin/stockfish + STOCKFISH_PATH
alembic upgrade head

# 4. Запустить backend
uvicorn app.main:app --reload
# → http://127.0.0.1:8000  (OpenAPI: /docs)
```

Фоновые выгрузки датасета (JSONL) в dev — отдельный `arq`-воркер:

```bash
docker compose up -d worker     # общий Redis и ./data с API
```

Frontend — отдельный пакет:

```bash
pnpm --dir web install
VITE_API_PROXY=http://127.0.0.1:8000 pnpm --dir web dev
# → http://127.0.0.1:5173
```

Тесты и E2E локально — [GETTING_STARTED.md §4.2](docs/development/GETTING_STARTED.md#42-запуск-тестов-и-e2e-локально).

### Аутентификация (JWT + HttpOnly refresh, итерации 24–26)

Публичный API защищён JWT: без `Authorization: Bearer <access>` защищённые
эндпоинты (`/v1/analyze`, `/v1/games*`, `/v1/commentary*`, `/v1/chat*`)
возвращают `401 unauthorized`. Access-токен живёт 15 минут, refresh — 7 дней.
Пароли хешируются **Argon2id** (`argon2-cffi`); legacy bcrypt-хеши
автоматически перехешируются при первом успешном входе.

```bash
# Регистрация (email + пароль 8..256 символов)
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"strong-password"}'

# Вход → access_token в JSON, refresh_token — в HttpOnly-cookie
TOKEN=$(curl -s -c cookies.txt -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"strong-password"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Свои партии (обычный пользователь видит только свои; admin — все)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/games

# Профиль; обновление access по refresh-cookie (ротация cookie)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/auth/me
curl -b cookies.txt -c cookies.txt -X POST http://127.0.0.1:8000/auth/refresh

# Выход: refresh попадает в Redis-denylist, cookie очищается
curl -b cookies.txt -c cookies.txt -X POST http://127.0.0.1:8000/auth/logout
```

В UI: страница `/login` (вход/регистрация), профиль и «Выйти» в шапке.
Access-токен хранится **только в памяти** SPA, refresh — в HttpOnly-cookie
(`SameSite=Lax`, `Path=/auth`, `Secure` при `AUTH_COOKIE_SECURE=true`); при
`401` клиент делает single-flight refresh по cookie и повторяет запрос.
Роль `admin`/`auditor` (выдаётся в БД) пускает в `/admin` по access-токену
(Bearer или cookie `access_token`); для автоматизации сохранён
`X-Admin-Token`, парольная форма — legacy fallback. Ограничения —
KI-056 (SMTP/2FA/SSO/лимит попыток) и KI-042 (legacy bcrypt).

### Production: Docker

```bash
# .env должен содержать секреты; POSTGRES_PASSWORD смените с dev-дефолта
docker compose -p chessannotator-prod -f docker-compose.prod.yml up -d --build
# → http://127.0.0.1:8000 — SPA + API в контейнере `app`;
#   `worker` (arq) поднимается отдельным сервисом с общим volume экспортов
```

Образ собирается multi-stage: backend (`app/`, миграции), frontend
(`web/dist/`) и production-зависимости. Entrypoint применяет миграции
**до** старта приложения; SPA отдаёт `StaticFiles`; в compose также
`worker` для фоновых выгрузок. Порты БД/Redis
опубликованы только на `127.0.0.1`. Для нестандартных портов:
`API_PORT=8011 PROD_POSTGRES_PORT=5435 PROD_REDIS_PORT=6382 docker compose ...`.

Vite проксирует `/v1` на `VITE_API_PROXY` (по умолчанию
`http://127.0.0.1:8000`), поэтому backend должен быть запущен первым.

### Observability: Grafana + Tempo + Prometheus

Стек наблюдаемости поднимается отдельным compose-файлом и не конфликтует
с основным:

```bash
docker compose -f docker-compose.observability.yml up -d
# Grafana → http://localhost:3000 (admin/admin), Prometheus :9090, Tempo :3200
# OTel Collector: gRPC :4317, HTTP :4318
```

В `.env` приложения — `OTEL_ENABLED=true`, `OTEL_EXPORTER=otlp`,
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` (в контейнере —
`http://otel-collector:4318`). Дашборд и alert rules провижинятся
автоматически; подробнее — [GETTING_STARTED.md §6.2](docs/development/GETTING_STARTED.md#62-observability-стек-grafana--tempo--prometheus-t-109b).

Подробное разворачивание, настройка провайдеров и troubleshooting —
в [GETTING_STARTED.md](docs/development/GETTING_STARTED.md).

---

## Документация

**Полный индекс:** [**docs/README.md**](docs/README.md) — навигация по ролям,
таблицам и разделам.

| Документ | Для кого | Что внутри |
|---|---|---|
| [**docs/README.md**](docs/README.md) | все | Навигационный хаб документации |
| [SPEC.md](docs/development/SPEC.md) | разработчики | Полная спецификация проекта (источник истины) |
| [USER_GUIDE.md](docs/user_guide/USER_GUIDE.md) | пользователи | Сценарий, интерфейс, три уровня, чат, правки |
| [ASSISTANT_LEVELS.md](docs/user_guide/ASSISTANT_LEVELS.md) | пользователи | Чем отличаются три уровня ассистента |
| [MANUAL_TESTING_GUIDE.md](docs/qa/MANUAL_TESTING_GUIDE.md) | QA | Ручные кейсы и чек-лист приёмки |
| [GETTING_STARTED.md](docs/development/GETTING_STARTED.md) | разработчики | Разворачивание проекта за ~20 минут |
| [ARCHITECTURE.md](docs/development/ARCHITECTURE.md) | разработчики | Архитектура v1.0.0: C4-диаграммы, потоки, API, локализация, фоновые выгрузки, Docker |
| [DESIGN_ADMIN_AND_OPS.md](docs/development/DESIGN_ADMIN_AND_OPS.md) | разработчики | Phase 7: админка, LLMOps, FinOps, guardrails |
| [LLM_EVAL.md](docs/development/reports/LLM_EVAL.md) | QA / продукт | LLM-as-judge: оценки и ограничения качества |
| [RULES.md](docs/development/RULES.md) | разработчики | Правила кода, docs, workflow и безопасности |
| [PROMPT_CURRENT.md](docs/development/PROMPT_CURRENT.md) | ассистент / DSH | Актуальные системные инструкции и метрики |
| [RELEASES.md](docs/development/RELEASES.md) | релиз-менеджер | Журнал итераций, метрики и итоговые отчёты |
| [CHANGELOG.md](docs/CHANGELOG.md) | все | История версий (Keep a Changelog) |
| [KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | все | Реестр известных проблем и ограничений |

> Полный индекс — [docs/README.md](docs/README.md).

---

## Статус проекта

| Показатель | Значение |
|---|---|
| Версия | **v1.2.0** (KI-030 **Closed** в итерации 48: LoRA 3B/7B — 2.87/3.33 против zero-shot 4B 4.40/10, возврат к 4B; пересборка golden dataset — T-104b; KI-060 — языковой дрейф 7B) |
| Итерация | **40 — Stripe Checkout + webhook (T-083), billing UI (T-084), FinOps из `users.tier` (T-085)**; итерации 1–39 закрыты |
| Backend-тесты | **1029 passed, 3 skipped, 1 xfailed** (85 файлов, прогон итерации 40, 132.22 с; skip — 2 cairo-кейса на Windows-dev и `DEEPSEEK_API_KEY`, `xfailed` — маркер KI-012) |
| Frontend-тесты | **132 passed** (13 файлов: chess.js FEN, board, форматирование, SSE-парсер, auth-store, auth-API, billing-API, auth-flow, PDF-API, eval, PGN-экспорт, варианты, загрузка PGN, стрелки; Vitest) |
| Уровней ассистента | **3** (beginner / club / gm) |
| Таблиц в БД | **16** (15 ревизий, head `f2b3c4d5e6a7`; `llm_calls` — durable-история вызовов LLM, `usage_daily` — FinOps-агрегат, `engine_cache` — кэш MultiPV Stockfish, `export_jobs` — фоновые выгрузки, `users.is_verified` — email-верификация, `users.tier` + `stripe_customer_id` — подписка) |
| Ingestion | PGN/FEN → `ParsedGame`; ECO (заголовок + таблица префиксов), фазы, флаги хода, повторы позиций |
| Engine | Stockfish 17 по UCI: MultiPV (1/3/5), глубина 12/18/28, бюджеты 0.2/1/5 с; классификация ходов; пул из 4 процессов; **кэш `engine_cache` (T-108)** — попадание по `(fen_hash, engine_version, depth, multipv)`, TTL 30 дней, запись best-effort, версия из UCI `id name` |
| API | `POST /auth/register\|login\|refresh\|logout`, `GET /auth/me`, **`GET /auth/verify-email`**, **`POST /auth/forgot-password`**, **`POST /auth/reset-password`**, **`POST /auth/logout-all`**; защищённые JWT: `POST /v1/analyze`, `GET /v1/games`, `GET /v1/games/{id}`, `GET /v1/users/me/usage`, `POST /v1/commentary`, `PATCH /v1/commentaries/{id}`, **`GET /v1/commentaries/{id}/export/pdf?language=ru\|en` (T-107/T-107b, итерация 35)**, `POST /v1/chat` (язык — `language`/`Accept-Language`), SSE-варианты `/commentary/stream` и `/chat/stream`, **`POST /v1/billing/create-checkout-session` / `create-portal-session` / `webhook` (T-083)**; rate limit 10/мин; `X-Admin-Token` для ingest; `GET /v1/admin/exports/edits.jsonl`; **фоновые выгрузки** `POST /v1/admin/exports/jobs` + `GET /jobs/{id}` + `GET /jobs/{id}/download` (arq, KI-050/KI-053); `/admin` (starlette-admin: RAG/комментарии/правки/аудит/LLM-evals + промпты + экспорт + **read-only `export_jobs` с фильтром по статусу** + **LLMOps-дашборд `/admin/llmops`**) |
| Auth | JWT access 15 мин + refresh 7 дней (**PyJWT**, HS256; `python-jose` заменён в T-110 из-за PYSEC-2026-4298); refresh — HttpOnly-cookie (`SameSite=Lax`, `Path=/auth`, `Secure` в prod), `/auth/logout` + Redis-denylist, ротация отзывает прежний jti; пароли **Argon2id** (argon2-cffi), legacy bcrypt перехешируется при входе; dummy-Argon2id для несуществующего email; роли `user/admin/auditor`; **email-верификация (KI-041):** `users.is_verified`, mock-письмо, `/auth/verify-email`, 403 `email_not_verified` для analyze/commentary; **сброс пароля** и **logout-all** (`revoked_before` + `iat_ms`); **frontend auth-UX (KI-055):** `/verify-email`, `/forgot-password`, `/reset-password`, экран «Подтвердите email» в `AuthGate`, меню профиля с «Выйти со всех устройств»; prod-валидатор секретов/CORS (T-110); изоляция партий по `games.user_id`; ограничения — KI-056 |
| LLM | `beginner`/`club` → LM Studio (`qwen/qwen3-4b-2507`), `gm` → DeepSeek (`deepseek-chat`); таймауты, ретраи, токены, прогрев; локальный комментарий 15–120 с |
| Prompts/Validator | Промпты 3 уровней в таблице `prompts` (seed из `.txt`, версия, редактирование в админке, hot-reload кэша); пакет фактов: `club` — топ-3 без PV, `gm` — до 10 с PV; валидатор SAN с retry |
| FinOps | Redis — горячий счётчик дневного бюджета: `usage:{date}:user:{id}` для JWT-пользователя (**T-083: tier из `users.tier`**, free 50k / pro 500k; `PRO_IPS` и роль больше не дают Pro), анонимно — `usage:{date}:ip:{ip}`; `429 token_budget_exceeded` + `X-RateLimit-Reset`; `cost_usd` по ценам из конфига; **durable `usage_daily`** (T-031b): best-effort upsert токенов/стоимости/запросов по `subject_type+subject_key+day`, `/v1/users/me/usage` — Redis + DB fallback (`max(redis, db)`), `scripts/aggregate_usage.py` — месячные суммы и retention 90 дней |
| Billing | **Stripe (T-083):** `POST /v1/billing/create-checkout-session` (Customer + Checkout, `{url}`), `POST /v1/billing/create-portal-session` (Customer Portal), `POST /v1/billing/webhook` (проверка `Stripe-Signature`, `checkout.session.completed` → `users.tier='pro'`, `customer.subscription.deleted` → `free`; идемпотентно); без ключей — 503 `billing_not_configured`; миграция `f2b3c4d5e6a7` (`tier`, `stripe_customer_id`); живой флоу — KI-059 |
| Security | Guardrails чата: NFKC, control/zero-width/bidi, лимит 2000, 7 RU/EN-эвристик, `422 prompt_injection` + аудит; в sysprompt — запрет раскрывать инструкции |
| Audit | Append-only `audit_logs` с hash-chain, advisory-lock, триггером `UPDATE/DELETE/TRUNCATE`; аудит промптов (атомарно с правкой), правок комментариев, инъекций и **выгрузки датасета (`dataset.export`: актор, фильтры, число строк, completed)**; read-only view + страница проверки целостности |
| Semantic cache | `semantic_cache` (vector 768, HNSW): exact `fen_hash+level+prompt_hash`, semantic cosine < 0.05, TTL 30 дней; `/commentary`, `/chat` и SSE; второй запрос — 0.030 с |
| LoRA | `scripts/export_dataset.py` — **schema_version=2** по умолчанию: `user` = пакет фактов (`build_fact_package`), `assistant` = `edited_text`, `metadata`/`fact_sheet_chars`; v1 — legacy-флаг. `scripts/train_lora.py` — реальный SFT (peft/transformers, QLoRA 4-bit, r=16/alpha=32/q_proj,v_proj, маскирование на assistant, `--dry-run`/`--check-env`, поддержка schema {1,2}); `LORA_ADAPTER_PATH` → adapter basename для vLLM; инструкция — [LORA_TRAINING.md](docs/development/reports/LORA_TRAINING.md); 3B/7B-адаптеры обучены и оценены в итерации 48 (2.87/3.33), отклонены, KI-030 **Closed**, KI-060 — языковой дрейф 7B |
| E2E | Playwright 1.64.1: **34 сценария** (`auth`, `auth-flow`, `analyze`, `billing`, `board-arrows`, `board-variant`, `chat-context`, `commentary`, `history`, `limits`, `pgn-export`, `pgn-upload`, `profile`, `pwa`) × chromium/mobile; mocked-режим **68 passed**, real-backend 5 passed (chromium, итерация 32); CI job `e2e` + trace/screenshot при падении; локально на Windows/DSH — `E2E_PREVIEW=1` (esbuild-кэш заблокирован); ограничение — KI-040 |
| Commentary | `POST /v1/commentary`: пакет фактов → LLM → валидатор (до 2 retry) → `commentaries`; `language`/`Accept-Language` (`ru`/`en`, T-106, английские промпты `*_en.txt`); `PATCH` пишет правку в `edits`; **PDF-экспорт** (`GET .../export/pdf?language=ru\|en`, fpdf2 + TTF-шрифт, английские подписи с итерации 35, диаграммы доски: финальная позиция + критические моменты, кнопка «Скачать PDF» в UI) |
| Chat | `POST /v1/chat`: вопрос → контекст анализа → LLM → валидатор → `{reply, tokens_used}`; `language` (`ru`/`en`, `app/prompts/chat_en.txt`, итерация 35), SSE-вариант |
| Локализация | UI RU/EN (`react-i18next`); комментарий, чат и PDF — `ru`/`en` (`language` → `Accept-Language` → `DEFAULT_LANGUAGE`); `detail` ошибок API — английский для известных `code` при `Accept-Language: en`; en-промпты файлами, факт-пакет ru; KI-058 **Closed** (остатки — в записи KI-058) |
| Админка | `/admin` (starlette-admin) + `/admin/llmops`: RAG/комментарии/правки/аудит/LLM-evals/`export_jobs` (read-only, фильтр по статусу), редактируемые промпты с версиями и hot-reload, RBAC (auditor read-only), legacy `X-Admin-Token`/пароль — fallback |
| Фоновые выгрузки | `export_jobs` (queued/running/done/failed) → `arq`-очередь Redis → worker пишет NDJSON в `EXPORT_DIR` (sha256, размер, строки), recovery `running>15 мин → failed`, retention 7 дней (`scripts/prune_export_jobs.py`), read-only view в админке |
| Frontend | React 19 + Vite + React Router: главная, `/history` (плейсхолдер «Подтвердите email, чтобы увидеть историю» для неподтверждённых, итерация 35), `/analysis/:id`, публичные `/verify-email`, `/forgot-password`, `/reset-password`, **`/billing/success|cancel` (T-084)**; `react-chessboard` 5.12.1, FEN из PGN через `chess.js`, RU/EN, Zustand + sessionStorage; история чата сохраняется и уходит в LLM; комментарий/чат стримятся постепенно; **кнопка «Скачать PDF»** с JWT-refresh; меню профиля с logout/logout-all; **профиль: «Моя подписка» + Stripe Portal** |
| UX | Skeleton-лоадеры, Error Boundary, mobile 1280/820/640/480, usage-badge `Free: 12k/50k` + модалка Pro с **«Оформить подписку» (T-084)**, страницы success/cancel, экран подтверждения email, SPA-fallback в production |
| LLM-evals | `llm_evaluations` (model, level, accuracy/usefulness/style), read-only view в админке; `scripts/evaluate_llm.py` пишет MD/JSON и БД |
| RAG | `POST /v1/ingest` (PGN, до 5 МБ, `X-Admin-Token`) → `annotated_games` (LM Studio 768; **624** записи: 102 `my-collection` + 522 `new_samples`, итерация 25); `find_similar_positions` по FEN; контекст для `gm`; eval — `evaluate_llm.py --source new_samples` |
| Motifs | Проходные пешки, слабые поля, открытые линии/диагонали, безопасность короля, вилки/связки, мат — в таблице `motifs` (до 6 на позицию) |
| Policy | `choose_recommended_move`: gm — топ-1, club — альтернатива при разнице < 20 cp, beginner — фильтр раннего ферзя и зевков |
| LLM-eval | 5 партий × club/gm, 3 шкалы; база **5.19/10** (9 из 10); baseline `club` после итерации 16 — **4.40/10** (V3, Qwen3-4B); LoRA в итерации 48 — 3B **2.87/10**, 7B **3.33/10** (2.80/3.40/3.80), отчёт [LLM_EVAL.md](docs/development/reports/LLM_EVAL.md); KI-030 **Closed**, 3B/7B отклонены, пересборка golden dataset — T-104b, KI-060 |
| Streaming | SSE `/v1/commentary/stream` и `/v1/chat/stream`: `meta`/`delta`/`done`/`error`, финальная SAN-валидация; UI печатает постепенно |
| Observability | **LLMOps (T-030):** durable `llm_calls` (provider/model/level, tokens/cost/latency/cache_hit/status, prompt_slug/version, actor/game/commentary) — запись best-effort после `complete`/`stream_generate` + warmup, `cache_hit=true` для semantic cache; W3C `traceparent` → `trace_id` в `request.state`, contextvars, structlog, заголовок **`X-Trace-Id`** и OTel-спанах (`http_request` + вложенные `llm_*`); спаны `validator.validate` и `rag.retrieve`; OTel-экспорт `console`/`otlp`, admin-view `llm_calls`; **LLMOps-дашборд `/admin/llmops`** (T-109): p50/p95/p99, error rate, токены/стоимость, cache-hit rate, топ-5; **Grafana/Tempo/Prometheus-стек** (T-109b): `docker-compose.observability.yml`, spanmetrics по `http.route`, дашборд p95/p99 + 5xx + tokens/cost, провижининг datasource'ов; **alert rules (T-113)** — HighErrorRate/HighLatency/LLMBudgetExceeded; **retention** — `scripts/prune_llm_calls.py` (KI-048) и `scripts/prune_export_jobs.py` (KI-054); ограничения — KI-052, KI-057 |
| KI-реестр | **60** записей: 26 Open, 34 Closed (KI-058 закрыт в итерации 35: локализация PDF/чата/ошибок API; KI-030 **Closed** в итерации 48: LoRA 3B/7B отклонены; KI-060 — языковой дрейф 7B) |
| CI | GitHub Actions: ruff, mypy, alembic (upgrade + check + roundtrip), pytest, typecheck, build, test, Playwright E2E, docker build + smoke `import app.main` (KI-051); **`publish-docker` по тегу `v*` → GHCR (`:vX.Y.Z`, `:X.Y.Z`, `:latest`)**; pip + pnpm store cache |
| Docker | multi-stage образ (Node → Python + SPA), `docker-compose.prod.yml` (app + worker + postgres + redis, общий volume экспортов), loopback-порты БД/Redis; **сервис `worker`** (`arq worker.WorkerSettings`) в dev/prod compose; публикация в GHCR готово к тегу `v1.0.0` (T-027) |
| Слепой тест | `scripts/generate_blind_test.py` — 3 партии × 3 уровня, слоты A/B/C (seed), `answer_form.md` + `answers_key.json` (итерация 35); сам тест с 10 шахматистами не проведён (T-024, вручную) |

---

## Лицензия

**Proprietary.** © 2026 RuChating (iilmchat). Все права защищены.
