# ChessAnnotator AI Commentary Engine

[![CI](https://github.com/iilmchat/chess-annotator/actions/workflows/ci.yml/badge.svg)](https://github.com/iilmchat/chess-annotator/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](docs/CHANGELOG.md)
[![Backend tests](https://img.shields.io/badge/backend_tests-724_passed-brightgreen.svg)](docs/development/RELEASES.md)
[![Frontend tests](https://img.shields.io/badge/frontend_tests-46_passed-brightgreen.svg)](docs/development/RELEASES.md)
[![License](https://img.shields.io/badge/license-proprietary-lightgrey.svg)](#лицензия)

> **Ассистент-комментатор шахматных партий.** Загрузите PGN — получите
> разбор уровня гроссмейстера, адаптированный под ваш уровень: новичок,
> клубный игрок или гроссмейстер.

**Версия:** `v0.1.0` · **Стек:** Python 3.12 + FastAPI · React 19 · PostgreSQL 17 + pgvector ·
Redis · Stockfish 17 · LM Studio / DeepSeek · Docker Compose
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
  моментов.
- **Motif Detector**: слабые поля, отсталые и проходные пешки, открытые
  линии и диагонали, безопасность короля, тактические темы (связка,
  вилка, отвлечение, промежуточный шах).
- **Policy Layer**: детерминированный выбор рекомендуемого хода под
  уровень пользователя (не просто top-1 движка).
- **Валидатор**: каждый вариант из LLM-ответа прогоняется через движок;
  нелегальные/ошибочные отбрасываются, LLM переписывает.

### 💬 Комментарий и чат

- **Commentary Generation**: LLM получает структурированный «пакет
  фактов» (оценки, варианты, мотивы, retrieved-аннотации) и пишет текст
  в стиле гроссмейстерских аннотаций (Лейтао, Каспаров, Шипов).
- **Chat**: диалог по партии с уточняющими вопросами («почему не работает
  этот ход?», «что было бы, если...»), с той же валидацией вариантов.
- **Human-in-the-loop**: веб-редактор правок; правки идут в датасет для
  дообучения и few-shot.

### 📚 RAG и база партий

- **Источники**: ChessBase Mega Database 2026 (при наличии лицензии),
  открытые аннотированные партии (Lichess Studies и др.), собственная
  растущая база через модуль загрузки.
- **Хранилище**: PostgreSQL 17 + `pgvector`, эмбеддинги позиций и текстов.
- **Retrieval**: top-k похожих позиций + их аннотации → в контекст LLM
  как «гроссмейстерский контекст».

### 🔐 Совместная работа и безопасность

- **Auth**: JWT + refresh-токены, сессии, защищённые маршруты.
- **Rate limiting**: Redis-счётчики, лимиты на login и общий API.
- **Приватность**: партии не уходят во внешние LLM при уровне «новичок»
  (локальная модель); для «гроссмейстера» — опция «только локально».
- **Секреты**: ENV + Vault/SOPS, AES-256-GCM для ключей провайдеров.

### 📈 Наблюдаемость

- **OpenTelemetry**: трейсы OTLP/HTTP, span `agent.step`, Pino-логи,
  per-provider semaphore.
- **Метрики**: покрытие тестами, время CI, стоимость комментария по
  уровням, латентность `/v1/analyze` и `/v1/commentary`.

### 🗄️ Хранилище

- **S3-совместимое** (AWS S3, MinIO, R2, Yandex Object Storage):
  presigned URL, TTL-очистка, multipart-загрузка.
- **PostgreSQL 17 + pgvector**: партии, ходы, мотивы, аннотации,
  комментарии, правки, retrieval-кеш.

---

## Стек

| Слой | Технологии |
|---|---|
| **Backend** | Python 3.12, FastAPI, `python-chess`, собственный async UCI-клиент Stockfish, Pydantic, structlog |
| **Frontend** | React 19, Vite 6, TypeScript, `react-chessboard` 5.12.1, `chess.js`, `react-i18next` (RU/EN), Zustand |
| **Данные** | PostgreSQL 17 + **pgvector**, SQLAlchemy 2 + Alembic |
| **Очереди** | `arq` (задел), Redis 7 |
| **Движки** | Stockfish 17 (LC0 — Backlog T-101) |
| **AI / LLM** | Локально: LM Studio + Qwen3-4B. Облако: DeepSeek |
| **RAG** | `pgvector`, LM Studio `text-embedding-nomic-embed-text-v1.5` (768, L2); hash — fallback тестов |
| **Хранилище** | локальная ФС (S3 — Backlog) |
| **Инфраструктура** | Docker Compose, OpenTelemetry-зависимости (трейсинг — Phase 7), SSE-стриминг |
| **Качество** | pytest, Vitest 2, `ruff`, `mypy` |

---

## Быстрый старт

**Требования:** Python 3.12+, Node.js 22+, pnpm 12+, Docker Desktop,
Stockfish 17 в PATH (или через Docker-контейнер).

```bash
# 1. Клонировать репозиторий
git clone https://github.com/iilmchat/chess-annotator.git
cd chess-annotator

# 2. Поднять PostgreSQL 17 + pgvector и Redis
docker compose up -d postgres redis
# Stockfish — локально в PATH или в .env: STOCKFISH_PATH=...

# 3. Установить зависимости и применить миграции
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head

# 4. Запустить backend
uvicorn app.main:app --reload
# → http://127.0.0.1:8000  (OpenAPI: /docs)
```

Frontend — отдельный пакет:

```bash
pnpm --dir web install
VITE_API_PROXY=http://127.0.0.1:8000 pnpm --dir web dev
# → http://127.0.0.1:5173
```

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
`X-Admin-Token`, парольная форма — legacy fallback. Ограничения — KI-041,
KI-042 (2FA/email-flow, RBAC внутри admin views, legacy bcrypt).

### Production: Docker

```bash
# .env должен содержать секреты; POSTGRES_PASSWORD смените с dev-дефолта
docker compose -p chessannotator-prod -f docker-compose.prod.yml up -d --build
# → http://127.0.0.1:8000 — SPA и API из одного контейнера
```

Образ собирается multi-stage: backend (`app/`, миграции), frontend
(`web/dist/`) и production-зависимости. Entrypoint применяет миграции
**до** старта приложения; SPA отдаёт `StaticFiles`. Порты БД/Redis
опубликованы только на `127.0.0.1`. Для нестандартных портов:
`API_PORT=8011 PROD_POSTGRES_PORT=5435 PROD_REDIS_PORT=6382 docker compose ...`.

Vite проксирует `/v1` на `VITE_API_PROXY` (по умолчанию
`http://127.0.0.1:8000`), поэтому backend должен быть запущен первым.

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
| [ARCHITECTURE.md](docs/development/ARCHITECTURE.md) | разработчики | Архитектура, контракты и Docker-развёртывание |
| [DESIGN_ADMIN_AND_OPS.md](docs/development/DESIGN_ADMIN_AND_OPS.md) | разработчики | Phase 7: админка, LLMOps, FinOps, guardrails |
| [LLM_EVAL.md](docs/development/reports/LLM_EVAL.md) | QA / продукт | LLM-as-judge: оценки и ограничения качества |
| [RULES.md](docs/development/RULES.md) | разработчики | Правила кода, docs, workflow и безопасности |
| [PROMPT_CURRENT.md](docs/development/PROMPT_CURRENT.md) | ассистент / DSH | Актуальные системные инструкции и метрики |
| [RELEASES.md](docs/development/RELEASES.md) | релиз-менеджер | Пошаговый чек-лист выпуска версии |
| [CHANGELOG.md](docs/CHANGELOG.md) | все | История версий (Keep a Changelog) |
| [KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | все | Реестр известных проблем и ограничений |

> Полный индекс — [docs/README.md](docs/README.md).

---

## Статус проекта

| Показатель | Значение |
|---|---|
| Версия | **v0.1.0** (MVP; качество LLM ниже 7/10 — KI-030) |
| Итерация | **31 — Observability, PDF Board Diagram и Background Export (T-107b, T-109b, KI-050)**; итерации 1–30 закрыты |
| Backend-тесты | **724 passed, 1 skipped, 1 xfailed** (68 файлов; skip — нет `DEEPSEEK_API_KEY`, `xfailed` — маркер KI-012) |
| Frontend-тесты | **46 passed** (7 файлов: chess.js FEN, board, форматирование, SSE-парсер, auth-store, auth-API, PDF-API; Vitest) |
| Уровней ассистента | **3** (beginner / club / gm) |
| Таблиц в БД | **16** (12 ревизий, head `b0c9d2f13425`; `llm_calls` — durable-история вызовов LLM, `usage_daily` — FinOps-агрегат, `engine_cache` — кэш MultiPV Stockfish, `export_jobs` — фоновые выгрузки датасета) |
| Ingestion | PGN/FEN → `ParsedGame`; ECO (заголовок + таблица префиксов), фазы, флаги хода, повторы позиций |
| Engine | Stockfish 17 по UCI: MultiPV (1/3/5), глубина 12/18/28, бюджеты 0.2/1/5 с; классификация ходов; пул из 4 процессов; **кэш `engine_cache` (T-108)** — попадание по `(fen_hash, engine_version, depth, multipv)`, TTL 30 дней, запись best-effort, версия из UCI `id name` |
| API | `POST /auth/register|login|refresh|logout`, `GET /auth/me`; защищённые JWT: `POST /v1/analyze`, `GET /v1/games`, `GET /v1/games/{id}`, `GET /v1/users/me/usage`, `POST /v1/commentary`, `PATCH /v1/commentaries/{id}`, **`GET /v1/commentaries/{id}/export/pdf` (T-107/T-107b)**, `POST /v1/chat`, SSE-варианты `/commentary/stream` и `/chat/stream`; rate limit 10/мин; `X-Admin-Token` для ingest; `GET /v1/admin/exports/edits.jsonl` (JSONL-экспорт, JWT admin/auditor или cookie админки); **фоновые выгрузки** `POST /v1/admin/exports/jobs` + `GET /jobs/{id}` + `GET /jobs/{id}/download` (KI-050); `/admin` (starlette-admin: RAG/комментарии/правки/аудит/LLM-evals + промпты + страница «Экспорт датасета» с формой фоновой джобы + **LLMOps-дашборд `/admin/llmops`**; вход по JWT admin/auditor) |
| Auth | JWT access 15 мин + refresh 7 дней (python-jose); refresh — HttpOnly-cookie (`SameSite=Lax`, `Path=/auth`, `Secure` в prod), `/auth/logout` + Redis-denylist, ротация отзывает прежний jti; пароли **Argon2id** (argon2-cffi), legacy bcrypt перехешируется при входе; dummy-Argon2id для несуществующего email (timing-safe login); роли `user/admin/auditor`; access на фронтенде только в памяти, `credentials: include`, single-flight refresh; изоляция партий по `games.user_id`; `/admin` — JWT admin/auditor, auditor read-only; ограничения — KI-041, KI-042 |
| LLM | `beginner`/`club` → LM Studio (`qwen/qwen3-4b-2507`), `gm` → DeepSeek (`deepseek-chat`); таймауты, ретраи, токены, прогрев; локальный комментарий 15–120 с |
| Prompts/Validator | Промпты 3 уровней в таблице `prompts` (seed из `.txt`, версия, редактирование в админке, hot-reload кэша); пакет фактов: `club` — топ-3 без PV, `gm` — до 10 с PV; валидатор SAN с retry |
| FinOps | Redis — горячий счётчик дневного бюджета: `usage:{date}:user:{id}` для JWT-пользователя (tier из роли: admin/auditor → pro, user → free), анонимно — `usage:{date}:ip:{ip}`/`PRO_IPS`; `429 token_budget_exceeded` + `X-RateLimit-Reset`; `cost_usd` по ценам из конфига; **durable `usage_daily`** (T-031b): best-effort upsert токенов/стоимости/запросов по `subject_type+subject_key+day`, `/v1/users/me/usage` — Redis + DB fallback (`max(redis, db)`), `scripts/aggregate_usage.py` — месячные суммы и retention 90 дней |
| Security | Guardrails чата: NFKC, control/zero-width/bidi, лимит 2000, 7 RU/EN-эвристик, `422 prompt_injection` + аудит; в sysprompt — запрет раскрывать инструкции |
| Audit | Append-only `audit_logs` с hash-chain, advisory-lock, триггером `UPDATE/DELETE/TRUNCATE`; аудит промптов (атомарно с правкой), правок комментариев, инъекций и **выгрузки датасета (`dataset.export`: актор, фильтры, число строк, completed)**; read-only view + страница проверки целостности |
| Semantic cache | `semantic_cache` (vector 768, HNSW): exact `fen_hash+level+prompt_hash`, semantic cosine < 0.05, TTL 30 дней; `/commentary`, `/chat` и SSE; второй запрос — 0.030 с |
| LoRA | `scripts/export_dataset.py` — **schema_version=2** по умолчанию: `user` = пакет фактов (`build_fact_package`), `assistant` = `edited_text`, `metadata`/`fact_sheet_chars`; v1 — legacy-флаг. `scripts/train_lora.py` — реальный SFT (peft/transformers, QLoRA 4-bit, r=16/alpha=32/q_proj,v_proj, маскирование на assistant, `--dry-run`/`--check-env`, поддержка schema {1,2}); `LORA_ADAPTER_PATH` → adapter basename для vLLM; инструкция — [LORA_TRAINING.md](docs/development/reports/LORA_TRAINING.md); обучение на GPU не выполнялось (нет CUDA, KI-030 Open) |
| E2E | Playwright 1.64.1: 5 сценариев (`auth`, `analyze`, `commentary`, `history`, `limits`) × chromium/mobile; mocked-режим 10 passed, real-backend 5 passed (chromium); CI job `e2e` + trace/screenshot при падении; ограничение — KI-040 |
| Commentary | `POST /v1/commentary`: пакет фактов → LLM → валидатор (до 2 retry) → `commentaries`; `PATCH` пишет правку в `edits`; **PDF-экспорт** (`GET .../export/pdf`, fpdf2 + TTF-шрифт, диаграммы доски: финальная позиция + критические моменты, кнопка «Скачать PDF» в UI) |
| Chat | `POST /v1/chat`: вопрос → контекст анализа → LLM → валидатор → `{reply, tokens_used}` |
| Frontend | React 19 + Vite + React Router: главная, `/history`, `/analysis/:id`; `react-chessboard` 5.12.1, FEN из PGN через `chess.js`, RU/EN, Zustand + sessionStorage; история чата сохраняется и уходит в LLM; комментарий/чат стримятся постепенно; **кнопка «Скачать PDF»** с JWT-refresh |
| UX | Skeleton-лоадеры, Error Boundary, mobile 1280/820/640/480, usage-badge `Free: 12k/50k` + модалка Pro, SPA-fallback в production |
| LLM-evals | `llm_evaluations` (model, level, accuracy/usefulness/style), read-only view в админке; `scripts/evaluate_llm.py` пишет MD/JSON и БД |
| RAG | `POST /v1/ingest` (PGN, до 5 МБ, `X-Admin-Token`) → `annotated_games` (LM Studio 768; **624** записи: 102 `my-collection` + 522 `new_samples`, итерация 25); `find_similar_positions` по FEN; контекст для `gm`; eval — `evaluate_llm.py --source new_samples` |
| Motifs | Проходные пешки, слабые поля, открытые линии/диагонали, безопасность короля, вилки/связки, мат — в таблице `motifs` (до 6 на позицию) |
| Policy | `choose_recommended_move`: gm — топ-1, club — альтернатива при разнице < 20 cp, beginner — фильтр раннего ферзя и зевков |
| LLM-eval | 5 партий × club/gm, 3 шкалы; база **5.19/10** (9 из 10); после итерации 16 `club` — **4.40/10** (было 3.83), отчёт [LLM_EVAL.md](docs/development/reports/LLM_EVAL.md); ниже 7/10 — KI-030 |
| Streaming | SSE `/v1/commentary/stream` и `/v1/chat/stream`: `meta`/`delta`/`done`/`error`, финальная SAN-валидация; UI печатает постепенно |
| Observability | **LLMOps (T-030):** durable `llm_calls` (provider/model/level, tokens/cost/latency/cache_hit/status, prompt_slug/version, actor/game/commentary) — запись best-effort после `complete`/`stream_generate` + warmup, `cache_hit=true` для semantic cache; W3C `traceparent` → `trace_id` в `request.state`, contextvars, structlog, заголовок **`X-Trace-Id`** и OTel-спанах (`http_request` + вложенные `llm_*`); спаны `validator.validate` и `rag.retrieve`; OTel-экспорт `console`/`otlp`, admin-view `llm_calls`; **LLMOps-дашборд `/admin/llmops`** (T-109): p50/p95/p99, error rate, токены/стоимость, cache-hit rate, топ-5; **Grafana/Tempo/Prometheus-стек** (T-109b): `docker-compose.observability.yml`, spanmetrics по `http.route`, дашборд p95/p99 + 5xx + tokens/cost, провижининг datasource'ов; **retention `llm_calls`** — `scripts/prune_llm_calls.py`; ограничения — KI-052, KI-053 |
| KI-реестр | **53** записи: 25 Open, 28 Closed (в итерации 31 закрыт KI-050 — фоновая выгрузка; добавлен KI-053 — джоба не переживает рестарт; KI-030 остаётся Open) |
| CI | GitHub Actions: ruff, mypy, alembic (upgrade + check + roundtrip), pytest, typecheck, build, test, Playwright E2E, docker build + smoke `import app.main` (KI-051); pip + pnpm store cache |
| Docker | multi-stage образ (Node → Python + SPA), `docker-compose.prod.yml`, loopback-порты БД/Redis; публикация в GHCR — T-027 |

---

## Лицензия

**Proprietary.** © 2026 RuChating (iilmchat). Все права защищены.
