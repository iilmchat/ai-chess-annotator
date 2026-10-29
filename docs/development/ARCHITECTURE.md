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
| `app/engine/` | async UCI-клиент Stockfish, пул, классификация, Policy, валидатор; `engine_cache` (T-108) |
| `app/export/` | PDF-экспорт комментария: fpdf2 + TTF-шрифт, шапка/ходы/текст; `board.py` — SVG доски → PNG через cairosvg с graceful degradation (T-107/T-107b) |
| `app/dataset/` | dataset-экспорт: `records.py` — общая выборка JSONL (фильтры/keyset/сборка записи), `jobs.py` — фоновая выгрузка `export_jobs` (KI-050) |
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
| `app/admin.py` | starlette-admin: JWT/legacy-аутентификация, RBAC, read-only views, аудит и экспорт |
| `app/llmops_dashboard.py` | LLMOps-дашборд `/admin/llmops`: p50/p95/p99, error rate, tokens/cost, cache hit rate, топ-5 пользователей |
| `app/observability.py` | OTel: TracerProvider/MeterProvider, OTLP-экспорт спанов и метрик `llm.*`; `app/tracing.py` — W3C trace-context, HTTP-спан с `http.route`/статусом для spanmetrics |
| `deploy/` | конфиги observability-стека: `otel-collector`, `tempo`, `prometheus`, `grafana` (datasources + дашборд) |
| `app/api/v1/` | `analyze`, `games`, `commentary`, `chat`, `ingest`, `health`, `admin/exports` |
| `app/db/` | SQLAlchemy-модели, сессии, Alembic-миграции |

## 3. Потоки

### 3.1. Анализ партии

```text
POST /v1/analyze
  → parse_pgn (python-chess: легальность, ECO, фазы)
  → EnginePool.analyze_game_moves (MultiPV по уровню)
     ↳ EngineCache: попадание по (fen_hash, engine_version, depth, multipv)
       → без запуска поиска; промах → Stockfish → best-effort store
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
| GET | `/v1/commentaries/{id}/export/pdf` | PDF комментария + диаграммы доски (T-107/T-107b) |
| GET | `/v1/admin/exports/edits.jsonl` | streaming NDJSON датасета (admin/auditor) |
| POST | `/v1/admin/exports/jobs` | фоновая выгрузка (202, admin/legacy) |
| GET | `/v1/admin/exports/jobs/{id}` | статус джобы |
| GET | `/v1/admin/exports/jobs/{id}/download` | готовый JSONL (409/410) |
| POST | `/v1/chat` | вопрос по `analysis_id` |
| POST | `/v1/chat/stream` | SSE-ответ чата |
| POST | `/v1/ingest` | PGN → `annotated_games` (`X-Admin-Token`) |
| GET | `/v1/health` | БД/Redis/Stockfish/LLM |
| GET | `/docs` | OpenAPI |

Ошибки — `{"detail", "code"}`; коды: 400/404/422/429/500/502/503/504.

## 5. База данных

16 таблиц: `users`, `games`, `moves`, `motifs`, `annotated_games`,
`retrieval_cache`, `commentaries`, `edits`, `prompts`, `audit_logs`,
`semantic_cache`, `llm_evaluations`, `llm_calls`, `usage_daily`,
`engine_cache`, `export_jobs`. Основное:

- `moves` — SAN/FEN/eval/best/delta/class/multipv;
- `motifs` — тип, описание, важность, FK на ход;
- `games` — PGN, ECO, фазы, `analysis_level`, `analysis_time_ms`;
- `annotated_games` — RAG: PGN, метаданные, аннотация, `vector(768)`
  (LM Studio nomic-embed);
- `commentaries` — текст, модель, токены, `validated`, `human_edited`;
- `edits` — пары «оригинал → правка» (датасет);
- `export_jobs` — фоновые выгрузки: статус, снимок фильтров, путь к
  JSONL в `EXPORT_DIR`, размер/sha256, число строк (KI-050).

Миграция — `b0c9d2f13425` (head); `alembic check` без дрейфа.

## 5.1. Observability

```text
app (OTel SDK) --OTLP/HTTP--> otel-collector --gRPC--> tempo (трейсы)
                                   |--spanmetrics--> :8890 --\
                                   |--OTLP metrics--> :8889 --> prometheus --> grafana
```

Стек поднимается `docker-compose.observability.yml` (Grafana :3000,
Prometheus :9090, Tempo :3200, collector :4317/:4318). Дашборд
`deploy/grafana/dashboards/chessannotator-observability.json` провижинится
автоматически: latency p95/p99 `/v1/commentary` и `/v1/analyze`, error
rate 5xx, стоимость и токены LLM. HTTP-спан `http_request` несёт
`http.route` (полный, с префиксом) и `http.response.status_code`; при
`OTEL_ENABLED=true` спаны и метрики уходят в коллектор, при `false` —
no-op.

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
