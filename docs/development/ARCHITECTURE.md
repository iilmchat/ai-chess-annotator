# ARCHITECTURE — ChessAnnotator v0.1.0

> Как устроена система сейчас. Источник истины по требованиям — `SPEC.md`;
> здесь — фактическая реализация MVP и границы.

## 1. Компоненты

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser (React SPA)                          │
│  PGN → /v1/analyze → доска/ходы → /v1/commentary → PATCH → /v1/chat │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP/JSON
┌───────────────────────────────▼─────────────────────────────────────┐
│                         FastAPI (app/)                              │
│  rate limit → routers → services → engine pool / LLM / RAG          │
│  StaticFiles("/") отдаёт web/dist (production)                      │
└───────┬─────────────────┬─────────────────┬─────────────────────────┘
        │                 │                 │
┌───────▼──────┐  ┌───────▼──────┐  ┌───────▼──────────┐
│ PostgreSQL 17│  │ Redis 7      │  │ Stockfish 17     │
│ + pgvector   │  │ rate limit   │  │ пул 4 процесса   │
└──────────────┘  └──────────────┘  └──────────────────┘
        │
┌───────▼─────────────────────────────────────────────────────────────┐
│ LLM: LM Studio (beginner/club) · DeepSeek (gm)                       │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Backend-модули

| Модуль | Ответственность |
| :--- | :--- |
| `app/ingestion/` | PGN/FEN → `ParsedGame`, ECO, фазы, повторы |
| `app/engine/` | async UCI-клиент Stockfish, пул, классификация, Policy, валидатор |
| `app/motifs/` | эвристики мотивов (слабые поля, линии, вилки, мат и т.д.) |
| `app/llm/` | OpenAI-совместимый клиент, фабрика по уровню, пакет фактов, прогрев |
| `app/prompts/` | системные промпты `beginner`/`club`/`gm`: `.txt` — seed/fallback, таблица `prompts` — источник истины, in-memory кэш |
| `app/finops.py` | дневные счётчики токенов в Redis, `TokenBudgetMiddleware` (429), `cost_usd` |
| `app/security/` | prompt-injection guardrails: нормализация, эвристики, `422 prompt_injection` |
| `app/audit.py` | append-only аудит, hash-chain, advisory-lock, верификация |
| `app/cache/` | semantic cache: exact FEN-ключ, vector-поиск, TTL |
| `app/web.py` | SPA static + fallback на `index.html` для `/history`, `/analysis/:id` |
| `app/commentary/` | сборка анализа из БД, генерация, retry, кэш, правка |
| `app/chat/` | вопрос → контекст → LLM → валидатор → ответ |
| `app/rag/` | ingest аннотированных партий, эмбеддинги, retrieval, контекст |
| `app/api/v1/` | `analyze`, `games`, `commentary`, `chat`, `ingest`, `health` |
| `app/db/` | SQLAlchemy-модели, сессии, Alembic-миграции |

## 3. Потоки

### 3.1. Анализ партии

```text
POST /v1/analyze
  → parse_pgn (python-chess: легальность, ECO, фазы)
  → EnginePool.analyze_game_moves (MultiPV по уровню)
  → detect_motifs + classify_move + choose_recommended_move
  → INSERT games/moves/motifs
  → JSON: analysis_id, initial_fen, moves[] с fen/оценкой/мотивами
```

### 3.2. Комментарий

```text
POST /v1/commentary
  → load_game + load_analyses (moves/motifs/multipv)
  → build_fact_package (метаданные, ходы, ключевые моменты)
  → [+ RAG-контекст для gm]
  → get_llm_client(level).complete
  → extract_moves_from_text + validate_moves
  → retry (до 2) при нелегальных ходах
  → INSERT commentaries
  → JSON: text, tokens_used, validated, model, attempts
```

### 3.3. Чат

```text
POST /v1/chat
  → те же load_game/load_analyses + build_fact_package
  → CHAT_SYSTEM_PROMPT + <вопрос>
  → LLM → validator (mainline + PV positions) → retry
  → JSON: reply, tokens_used
```

### 3.4. Правка

```text
PATCH /v1/commentaries/{id}
  → SELECT ... (original_text)
  → UPDATE text, human_edited=true
  → INSERT edits(original_text, edited_text)
  → JSON: комментарий + edit_id
```

## 4. API v0.1.0

| Метод | Путь | Назначение |
| :--- | :--- | :--- |
| POST | `/v1/analyze` | PGN + level → анализ, `analysis_id`, `analysis_level` |
| GET | `/v1/games/{id}` | сохранённый анализ + `commentaries` без Stockfish |
| POST | `/v1/commentary` | `analysis_id` + level → комментарий |
| POST | `/v1/commentary/stream` | SSE: `meta`/`delta`/`done`/`error` |
| PATCH | `/v1/commentaries/{id}` | ручная правка + `edits` |
| POST | `/v1/chat` | вопрос по `analysis_id` |
| POST | `/v1/chat/stream` | SSE-ответ чата |
| POST | `/v1/ingest` | PGN → `annotated_games` (`X-Admin-Token`) |
| GET | `/v1/health` | БД/Redis/Stockfish/LLM |
| GET | `/docs` | OpenAPI |

Ошибки — `{"detail", "code"}`; коды: 400/404/422/429/500/502/503/504.

## 5. База данных

8 таблиц: `users`, `games`, `moves`, `motifs`, `annotated_games`,
`retrieval_cache`, `commentaries`, `edits`. Основное:

- `moves` — SAN/FEN/eval/best/delta/class/multipv;
- `motifs` — тип, описание, важность, FK на ход;
- `games` — PGN, ECO, фазы, `analysis_level`, `analysis_time_ms`;
- `annotated_games` — RAG: PGN, метаданные, аннотация, `vector(768)`
  (LM Studio nomic-embed);
- `commentaries` — текст, модель, токены, `validated`, `human_edited`;
- `edits` — пары «оригинал → правка» (датасет).

Миграция — `d2e3f4a5b6c7`; `alembic check` без дрейфа.

## 6. Frontend

- React 19 + TypeScript + Vite + React Router; `react-chessboard`
  5.12.1 для доски, `chess.js` (`history({verbose:true})`) для FEN из
  PGN, `react-i18next` (RU/EN), Zustand с persist в `sessionStorage`.
- Маршруты: `/` (PGN + анализ), `/history` (последние 20),
  `/analysis/:id` (загрузка сохранённого анализа). `AnalysisWorkspace`
  общий для главной и карточки.
- История чата — `chatStore` (Zustand, `sessionStorage`), уходит в
  `/v1/chat` и `/v1/chat/stream` как `history`; usage-badge опрашивает
  `/v1/users/me/usage`.
- API-клиент — `web/src/api.ts`; SSE читается через `ReadableStream`,
  текст печатается постепенно.
- Production: `pnpm build` → `web/dist` → `SPAStaticFiles("/")` в FastAPI
  (index.html для `/history` и `/analysis/:id`).
- UX: skeleton-лоадеры, Error Boundary, mobile 1280/820/640/480.
- Тесты — Vitest: FEN/chess.js, SSE-парсер, форматирование (20 тестов).

## 7. Развёртывание

- `Dockerfile`: multi-stage — Node собирает SPA, Python-образ получает
  `web/dist`, Stockfish, зависимости и запускается non-root.
- `docker-compose.prod.yml`: `app`, `postgres`, `redis`; БД и Redis
  опубликованы **только на 127.0.0.1** (или не публикуются), API — на
  loopback за reverse-proxy.
- `app` при старте выполняет `alembic upgrade head`, затем uvicorn.
- Healthcheck — `/v1/health`.

## 8. Безопасность и ограничения MVP

- JWT/ролей нет: `/v1/ingest` защищён общим `X-Admin-Token`
  (constant-time), полноценный RBAC — T-028.
- Rate limit 10/мин на IP, fail-open при недоступном Redis (KI-015).
- Валидатор проверяет легальность SAN, не семантику (KI-023).
- RAG-эмбеддинги — LM Studio 768 (L2); hash — fallback тестов.
- SSE-стриминг реализован; история чата между запросами не сохраняется.
- Слепой тест уровней (T-024) не проводился.

## 9. Что дальше

Phase 7 (`docs/development/DESIGN_ADMIN_AND_OPS.md`): admin panel, prompt
registry, OTel/LLMOps, FinOps, semantic cache, prompt-injection guardrails,
audit. После этого — multi-tenant и SSO (Backlog T-111…T-119).
