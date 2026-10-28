# ChessAnnotator AI Commentary Engine

[![CI](https://github.com/iilmchat/chess-annotator/actions/workflows/ci.yml/badge.svg)](https://github.com/iilmchat/chess-annotator/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](docs/CHANGELOG.md)
[![Backend tests](https://img.shields.io/badge/backend_tests-257_passed-brightgreen.svg)](docs/development/RELEASES.md)
[![Frontend tests](https://img.shields.io/badge/frontend_tests-20_passed-brightgreen.svg)](docs/development/RELEASES.md)
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
| Итерация | **18 — Security Guardrails + Audit (Фаза 7)**; итерации 1–17 закрыты |
| Backend-тесты | **257 passed, 1 xfailed** (32 файла; `xfailed` — маркер KI-012) |
| Frontend-тесты | **20 passed** (4 файла: chess.js FEN, react-chessboard-обвязка, SSE-парсер, форматирование; Vitest) |
| Уровней ассистента | **3** (beginner / club / gm) |
| Таблиц в БД | **10** (5 ревизий, head `f4a5b6c7d8e9`; `prompts`, `audit_logs`, `annotated_games.embedding vector(768)`) |
| Ingestion | PGN/FEN → `ParsedGame`; ECO (заголовок + таблица префиксов), фазы, флаги хода, повторы позиций |
| Engine | Stockfish 17 по UCI: MultiPV (1/3/5), глубина 12/18/28, бюджеты 0.2/1/5 с; классификация ходов; пул из 4 процессов |
| API | `POST /v1/analyze`, `GET /v1/games/{id}`, `POST /v1/commentary`, `PATCH /v1/commentaries/{id}`, `POST /v1/chat`, SSE-варианты `/commentary/stream` и `/chat/stream`; rate limit 10/мин; `X-Admin-Token` для ingest; `/admin` (starlette-admin: read-only RAG/комментарии/правки + редактируемые промпты) |
| LLM | `beginner`/`club` → LM Studio (`qwen/qwen3-4b-2507`), `gm` → DeepSeek (`deepseek-chat`); таймауты, ретраи, токены, прогрев; локальный комментарий 15–120 с |
| Prompts/Validator | Промпты 3 уровней в таблице `prompts` (seed из `.txt`, версия, редактирование в админке, hot-reload кэша); пакет фактов: `club` — топ-3 без PV, `gm` — до 10 с PV; валидатор SAN с retry |
| FinOps | Дневные лимиты токенов в Redis (`usage:{date}:ip`), `429 token_budget_exceeded` + `X-RateLimit-Reset`; `pro` — только IP из `PRO_IPS`, заголовок `X-User-Tier` игнорируется; `cost_usd` по ценам из конфига |
| Security | Guardrails чата: NFKC, control/zero-width/bidi, лимит 2000, 7 RU/EN-эвристик, `422 prompt_injection` + аудит; в sysprompt — запрет раскрывать инструкции |
| Audit | Append-only `audit_logs` с hash-chain, advisory-lock, триггером `UPDATE/DELETE/TRUNCATE`; аудит промптов, правок комментариев и инъекций; read-only view + страница проверки целостности |
| Commentary | `POST /v1/commentary`: пакет фактов → LLM → валидатор (до 2 retry) → `commentaries`; `PATCH` пишет правку в `edits` |
| Chat | `POST /v1/chat`: вопрос → контекст анализа → LLM → валидатор → `{reply, tokens_used}` |
| Frontend | React 19 + Vite: `react-chessboard` 5.12.1, FEN из PGN через `chess.js`, RU/EN, Zustand + sessionStorage; комментарий/чат стримятся и печатаются постепенно |
| RAG | `POST /v1/ingest` (PGN, до 5 МБ, `X-Admin-Token`) → `annotated_games` (LM Studio 768); `find_similar_positions` по FEN; контекст для `gm` |
| Motifs | Проходные пешки, слабые поля, открытые линии/диагонали, безопасность короля, вилки/связки, мат — в таблице `motifs` (до 6 на позицию) |
| Policy | `choose_recommended_move`: gm — топ-1, club — альтернатива при разнице < 20 cp, beginner — фильтр раннего ферзя и зевков |
| LLM-eval | 5 партий × club/gm, 3 шкалы; база **5.19/10** (9 из 10); после итерации 16 `club` — **4.40/10** (было 3.83), отчёт [LLM_EVAL.md](docs/development/reports/LLM_EVAL.md); ниже 7/10 — KI-030 |
| Streaming | SSE `/v1/commentary/stream` и `/v1/chat/stream`: `meta`/`delta`/`done`/`error`, финальная SAN-валидация; UI печатает постепенно |
| Observability | OTel-спаны `llm_generate`/`llm_stream` (`llm.model`, `llm.level`, `tokens_in/out`, `cost_usd`); `OTEL_ENABLED=true`, экспорт `console`/`otlp` |
| KI-реестр | **33** записи: 18 Open, 15 Closed (KI-031/KI-032 закрыты; новый KI-033) |
| CI | GitHub Actions: ruff, mypy, alembic (upgrade + check + roundtrip), pytest, typecheck, build, test, docker build |
| Docker | multi-stage образ (Node → Python + SPA), `docker-compose.prod.yml`, loopback-порты БД/Redis; публикация в GHCR — T-027 |

---

## Лицензия

**Proprietary.** © 2026 RuChating (iilmchat). Все права защищены.
