# ChessAnnotator AI Commentary Engine

[![CI](https://github.com/iilmchat/chess-annotator/actions/workflows/ci.yml/badge.svg)](https://github.com/iilmchat/chess-annotator/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](docs/CHANGELOG.md)
[![Backend tests](https://img.shields.io/badge/backend_tests-130_passed-brightgreen.svg)](docs/development/RELEASES.md)
[![Frontend tests](https://img.shields.io/badge/frontend_tests-0_yet-orange.svg)](docs/development/RELEASES.md)
[![License](https://img.shields.io/badge/license-proprietary-lightgrey.svg)](#лицензия)

> **Ассистент-комментатор шахматных партий.** Загрузите PGN — получите
> разбор уровня гроссмейстера, адаптированный под ваш уровень: новичок,
> клубный игрок или гроссмейстер.

**Версия:** `v0.1.0` · **Стек:** Python 3.12 + FastAPI · React 19 · PostgreSQL 17 + pgvector ·
Redis · Stockfish 17 · vLLM · OpenTelemetry · Docker/GHCR
**© 2026 RuChating (iilmchat)**

---

## Что это

ChessAnnotator — веб-приложение с API, которое принимает шахматную партию
(PGN/FEN) и возвращает **связный комментарий гроссмейстерского качества**.
Архитектура гибридная: движок (Stockfish / Leela Chess Zero) и база
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
- **Engine Service**: Stockfish 17 (и LC0 для гроссмейстерского уровня),
  MultiPV, классификация ходов (blunder / mistake / inaccuracy / good /
  best / brilliant), поиск критических моментов.
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
| **Backend** | Python 3.12, FastAPI, `python-chess`, `stockfish` (py), Zod-аналог (`pydantic`), Pino-аналог (`structlog`) |
| **Frontend** | React 19, Vite 8, `@xyflow/react` 12 (граф партии), Zustand 5, Tailwind CSS 4, react-i18next |
| **Данные** | PostgreSQL 17 + **pgvector**, SQLAlchemy 2 + Alembic |
| **Очереди** | `arq` (async Redis queue), Redis 6 |
| **Движки** | Stockfish 17, Leela Chess Zero (опционально) |
| **AI / LLM** | Локально: vLLM + Qwen3 / Llama-3.1. Облако: DeepSeek-V3.2 / GPT-5 / Claude Sonnet |
| **RAG** | `pgvector`, `bge-m3` / `text-embedding-3-large` |
| **Хранилище** | S3-совместимое (AWS SDK / boto3), локальная ФС |
| **Инфраструктура** | Docker Compose, **OpenTelemetry**, SSE |
| **Качество** | pytest, Vitest 4, Testing Library, `ruff`, `mypy`, Husky + lint-staged |

---

## Быстрый старт

**Требования:** Python 3.12+, Node.js 22+, pnpm 12+, Docker Desktop,
Stockfish 17 в PATH (или через Docker-контейнер).

```bash
# 1. Клонировать репозиторий
git clone https://github.com/iilmchat/chess-annotator.git
cd chess-annotator

# 2. Поднять PostgreSQL 17 + pgvector, Redis и Stockfish
docker compose up -d postgres redis stockfish

# 3. Установить зависимости и применить миграции
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head

# 4. Запустить backend
uvicorn app.main:app --reload
# → http://localhost:8000  (OpenAPI: /docs)
```

Frontend — отдельный пакет:

```bash
pnpm --dir web install
pnpm --dir web dev
# → http://127.0.0.1:5173
```

### Production: Docker

```bash
# .env должен содержать секреты; POSTGRES_PASSWORD смените с dev-дефолта
docker compose -f docker-compose.prod.yml up -d --build
# → http://localhost:8000 — SPA и API из одного контейнера
```

Образ собирается multi-stage: backend (`app/`, миграции), frontend
(`web/dist/`) и production-зависимости. Entrypoint применяет миграции
**до** старта приложения; SPA отдаёт `StaticFiles`. Порты БД/Redis
проброшены только на loopback. В production-compose включён
`COOKIE_SECURE=true`, поэтому вход рассчитан на HTTPS/reverse proxy.

Vite проксирует `/v1/*`, `/auth/*`, `/chat/*` и `/runs/*` (SSE) на
`http://localhost:8000`, поэтому backend должен быть запущен первым.

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
| [USER_GUIDE.md](docs/user_guide/USER_GUIDE.md) | пользователи | Быстрый старт, интерфейс, три уровня, чат, бюджет |
| [ASSISTANT_LEVELS.md](docs/user_guide/ASSISTANT_LEVELS.md) | пользователи | Чем отличаются три уровня ассистента |
| [MANUAL_TESTING_GUIDE.md](docs/qa/MANUAL_TESTING_GUIDE.md) | QA | Ручные кейсы и чек-лист приёмки |
| [GETTING_STARTED.md](docs/development/GETTING_STARTED.md) | разработчики | Разворачивание проекта за ~20 минут |
| [ARCHITECTURE.md](docs/development/ARCHITECTURE.md) | разработчики | Архитектура, контракты и Docker-развёртывание |
| [DESIGN v0.1](docs/development/v0.1/DESIGN_V010.md) | разработчики | MVP: scope, схема БД, API, промпты |
| [RULES.md](docs/development/RULES.md) | разработчики | Правила кода, docs, workflow и безопасности |
| [PROMPT_CURRENT.md](docs/development/PROMPT_CURRENT.md) | ассистент / DSH | Актуальные системные инструкции и метрики |
| [RELEASES.md](docs/development/RELEASES.md) | релиз-менеджер | Пошаговый чек-лист выпуска версии |
| [CHANGELOG.md](docs/CHANGELOG.md) | все | История версий (Keep a Changelog) |
| [KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) | все | Реестр известных проблем и ограничений |

> Часть ссылок в таблице выше ведёт на документы, которые появятся к T-026
> (`USER_GUIDE.md`, `ASSISTANT_LEVELS.md`, `MANUAL_TESTING_GUIDE.md`,
> `ARCHITECTURE.md`, `DESIGN_V010.md`). Актуальный статус документации и
> список «планируется» — в [docs/README.md](docs/README.md); расхождение
> зафиксировано в KI-009.

---

## Статус проекта

| Показатель | Значение |
|---|---|
| Версия | **v0.1.0** (MVP, в разработке) |
| Итерация | **6 — API с мотивами + LLM-адаптер (T-012, T-013)** завершена; итерации 1–5 закрыты |
| Backend-тесты | **130 passed, 1 xfailed** (11 файлов: health, schema, unit ingestion/engine/pool/rate-limit/motifs/policy/llm, integration analyze + live LLM; `xfailed` — маркер KI-012) |
| Frontend-тесты | **0** (0 файлов) — CI использует `--passWithNoTests`, см. KI-008 |
| Уровней ассистента | **3** (beginner / club / gm) |
| Таблиц в БД | **8** (`alembic` ревизия `b802598c3778`) |
| Ingestion | PGN/FEN → `ParsedGame`; ECO (заголовок + таблица префиксов), фазы, флаги хода, повторы позиций |
| Engine | Stockfish 17.1 по UCI: MultiPV (1/3/5), глубина 12/18/28, бюджеты 0.2/1/5 с; классификация ходов; пул из 4 процессов |
| API | `POST /v1/analyze`: PGN + уровень → ходы с оценками, мотивами и `recommended_move`; сохранение в `games`/`moves`/`motifs`; SLA 53 полухода «клуб» ≈ 15 с (лимит 30 с); rate limit 10/мин |
| LLM | `beginner`/`club` → LM Studio (`qwen/qwen3-4b-2507`, ≈1 c), `gm` → DeepSeek (`deepseek-chat`); таймауты, ретраи, учёт токенов |
| Motifs | Проходные пешки, слабые поля, открытые линии/диагонали, безопасность короля, вилки/связки, мат — в таблице `motifs` (до 6 на позицию) |
| Policy | `choose_recommended_move`: gm — топ-1, club — альтернатива при разнице < 20 cp, beginner — фильтр раннего ферзя и зевков |
| Ключевых моментов в анализе | определяется движком (delta ≥ 200 cp) |
| KI-реестр | **22** записи: 19 Open, 3 Closed (KI-014, KI-018, KI-020) |
| CI | GitHub Actions: ruff, mypy, alembic (upgrade + check + roundtrip), pytest, typecheck, build, test |
| Docker | multi-stage образ + `docker-compose.prod.yml`, публикация в GHCR |

---

## Лицензия

**Proprietary.** © 2026 RuChating (iilmchat). Все права защищены.
