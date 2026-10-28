# STEP-01 — Синхронизация UI и SSE-стриминг (итерация 15)

**Дата:** 2026-10-28
**Итерация:** 15
**Задачи:** sync Итераций 10–12, T-032 (SSE)
**Статус:** ✅

## 1. Проверка и восстановление Итераций 10–12

| Заявлено | Факт до итерации | Сделано |
| :--- | :--- | :--- |
| `react-chessboard` 5.12.1, `<Chessboard options={{position}} />` | не было, своя доска | установлен 5.12.1, Board переписан |
| FEN из PGN через `chess.js` `history({verbose:true})` | не было | `web/src/lib/pgn.ts`, оба `before`/`after` |
| `react-i18next` RU/EN | не было | i18n-ресурсы, переключатель RU/EN |
| Zustand `analysisStore.ts` + sessionStorage | не было | `stores/analysisStore.ts` с `persist` |
| `X-Admin-Token` для `/v1/ingest` | не было | `verify_admin_key`, constant-time compare |
| `LMStudioEmbedder` 768 + миграция | hash 1024 | `LMStudioEmbedder`, `vector(768)`, re-embed 102 |
| `GET /v1/games/{id}` | не было | endpoint + `analysis_level`/`commentaries` |
| `scripts/ingest_pgn.py --limit` | был | проверен, без изменений |
| `PATCH /v1/commentaries/{id}` + `edits` | был | проверен тестами |
| `POST /v1/chat` + `build_fact_package` + validator | был | проверен тестами |

## 2. SSE-стриминг

### Бэкенд

- `BaseLLMClient.stream_generate` (fallback — один чанк) и
  `OpenAICompatibleClient.stream_generate`:
  `stream=true`, разбор `data:`-строк, `delta.content`, `[DONE]`.
- `POST /v1/commentary/stream` (`app/commentary/streaming.py`):
  `meta` → `delta`* → `done` | `error`; финальный SAN-валидатор; при
  нелегальных ходах запись не создаётся.
- `POST /v1/chat/stream` (`app/chat/streaming.py`) — то же для чата.
- `sse-starlette` добавлен в зависимости; оба пути в rate limiting.
- Токены в stream-ветке — оценка `chars/4` (`app/llm/tokens.py`), т.к.
  OpenAI-стрим не всегда отдаёт `usage`.

### Фронтенд

- `streamCommentary`/`streamChat` в `web/src/api.ts`: `fetch` +
  `ReadableStream`, парсер SSE-блоков, `AbortController` для «Стоп».
- `useTypewriter` — постепенная печать без сброса на каждом чанке.
- `CommentaryPanel` и `ChatPanel` показывают текст в реальном времени.

## 3. Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend | `pytest -q` | **205 passed, 1 xfailed** (196.6 с) |
| Frontend | `pnpm --dir web test` | **20 passed** (4 файла) |
| Types | `pnpm --dir web typecheck` | 0 |
| Build | `pnpm --dir web build` | JS 406.59 kB / CSS 8.76 kB (terser) |
| Lint | `ruff check .` | 0 |
| Mypy | `mypy app` | 0, 49 файлов |
| Alembic | `alembic check` / `current` | без дрейфа, `d2e3f4a5b6c7` |
| Embeddings | `vector_dims` / NULL | 768 / 0 |
| Live stream | LM Studio `stream_generate` | чанки `Готов`/`о`, 1.05 с |
| Live chat SSE | `/v1/chat/stream`, `analysis_id=25` | 200, 16.7 с, 23 delta, `done`, 961 токен |
| SSE endpoints | `test_streaming.py` | 3 passed |
| Prod image | `docker build` | 513 MB, app `healthy` |
| Prod migrations | app entrypoint | `b802 → c1f2 → d2e3` |
| Prod SPA/GET/SSE | `8011` | `/` 200; `/v1/games/1` 200; `/v1/chat/stream` 200, 520 токенов |

## 4. Закрытые KI

- **KI-024** — SSE снимает блокировку UI; скорость модели не изменилась.
- **KI-025** — LMStudioEmbedder 768, миграция, 102 партии переиндексированы.
- **KI-026** — `X-Admin-Token` + constant-time; полноценный RBAC — T-028.
- **KI-027** — UI/бэкенд-фичи восстановлены.
- **KI-028** — SSE-чат и стриминг комментария.

KI-030 (качество LLM 5.19/10) остаётся открытым.

## 5. Ограничения

- История чата между запросами не сохраняется.
- `react-chessboard` добавлен как зависимость; Playwright/скриншот-тест
  по-прежнему не внедрён.
- Оценка токенов в SSE приблизительная (`chars/4`).
- Качество комментариев ниже 7/10 (KI-030), не блокер текущей итерации.

## 6. Что дальше

Production-образ пересобран (513 MB), миграции `c1f2→d2e3` применены,
`/v1/health` = `ok`; затем Phase 7 (T-028).
