# MANUAL TESTING GUIDE — ChessAnnotator v0.1.0

Ручные проверки MVP перед релизом. Автотесты — `pytest -q` и
`pnpm --dir web test`; здесь — то, что видит пользователь.

## 0. Подготовка

```bash
docker compose up -d postgres redis
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8010
# отдельный терминал:
VITE_API_PROXY=http://127.0.0.1:8010 pnpm --dir web dev
```

Открыть `http://127.0.0.1:5173/`. Для production-проверки — §7.

## 1. Healthcheck

```bash
curl -s http://127.0.0.1:8010/v1/health | jq
```

Ожидание: 200; `db/redis/stockfish = ok`; `llm` — `ok` или `skipped`;
общий статус `ok`/`degraded` (degraded допустим без LLM).

## 2. Анализ

1. Вставить PGN из `tests/fixtures/example.pgn`.
2. Уровень `club`, нажать **«Разобрать партию»**.
3. Ожидание: доска, 53 хода, сводка, `time_taken_ms ≤ 30000`.

```bash
curl -s -X POST http://127.0.0.1:8010/v1/analyze \
  -H 'Content-Type: application/json' \
  -d "{\"pgn\":$(jq -Rs . tests/fixtures/example.pgn),\"level\":\"club\"}" \
  | jq '{analysis_id, moves_analyzed, time_taken_ms, summary, initial_fen}'
```

Ожидание: `analysis_id` — число строкой, `moves_analyzed=53`,
`initial_fen` начинается с `rnbqkbnr`.

Сохранённый анализ:

```bash
curl -s http://127.0.0.1:8010/v1/games/<analysis_id> | jq \
  '{analysis_level, moves_analyzed, commentaries: (.commentaries|length)}'
```

Ожидание: `analysis_level=club`, 53 хода, список комментариев (может быть пуст).

## 3. Доска и ходы

1. Кликнуть по первому ходу — подсветка, карточка хода.
2. Проверить: SAN, оценка, лучший ход, потеря, мотивы, рекомендация.
3. Кликнуть **«Начало»** — стартовая позиция.
4. Проверить, что доска не «прыгает» и фигуры читаемы на обеих клетках.

## 4. Комментарий

1. Нажать **«Сгенерировать»** (уровень `club`).
2. Ожидание: текст, модель, токены, `валидирован`, число попыток.
3. Проверить: нет Markdown-разметки внутри SAN, нет выдуманных ходов
   (валидатор).
4. Повторить для `gm` (DeepSeek), если задан ключ.

SSE:

```bash
curl -N -X POST http://127.0.0.1:8010/v1/commentary/stream \
  -H 'Content-Type: application/json' \
  -d '{"analysis_id":25,"level":"club"}'
```

Ожидание: `event: meta`, затем несколько `event: delta`, последний —
`event: done` (или `event: error` при нелегальных ходах). В UI текст
появляется постепенно; кнопка «Остановить» прерывает поток.

## 5. Правка и `edits`

1. **«Редактировать»** → изменить текст → **«Сохранить правку»**.
2. Ожидание: `edit #N`, текст обновился.
3. Проверить в БД:

```bash
docker compose exec postgres psql -U chess -d chessannotator -c \
  "select id, human_edited, left(text,60) from commentaries order by id desc limit 1;"
docker compose exec postgres psql -U chess -d chessannotator -c \
  "select id, commentary_id, length(original_text), length(edited_text) from edits order by id desc limit 1;"
```

## 6. Чат

1. Вопрос **«Кто играл белыми и чёрными?»** → имена из PGN.
2. Вопрос **«Почему ход 12.Bb3 был ошибкой?»** → ссылка на оценку/лучший ход.
3. Проверить, что все SAN-ходы в ответе легальны (валидатор).
4. Отправить вопрос до анализа — поле/кнопка недоступны.
5. SSE-вариант:

```bash
curl -N -X POST http://127.0.0.1:8010/v1/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"analysis_id":25,"level":"club","message":"Кто играл белыми?"}'
```

Ожидание: `meta` → `delta`* → `done`; при нелегальном ходе — `error`.

## 7. Production Docker

```bash
docker compose -f docker-compose.prod.yml config >/dev/null
docker compose -p chessannotator-prod -f docker-compose.prod.yml build
API_PORT=8011 PROD_POSTGRES_PORT=5435 PROD_REDIS_PORT=6382 \
  docker compose -p chessannotator-prod -f docker-compose.prod.yml up -d
curl -s http://127.0.0.1:8011/v1/health | jq
curl -s http://127.0.0.1:8011/ | head -n 5
```

Ожидание: health 200; `/` отдаёт HTML SPA; порты БД/Redis — только
`127.0.0.1` (см. `docker ps`).

## 8. Ошибки и лимиты

| Кейс | Ожидание |
| :--- | :--- |
| Пустой PGN | 422/400 `invalid_pgn` |
| Нелегальный ход в PGN | 400 `invalid_pgn` |
| `analysis_id` не существует | 404 `not_found` |
| `level=master` | 422 |
| 11 запросов `/v1/analyze` за минуту | 429 + `Retry-After` |
| Нет LM Studio, уровень `club` | 503 `llm_error` |
| Нет `DEEPSEEK_API_KEY`, уровень `gm` | 503 `llm_not_configured` |
| Модель выдумала ход | SSE `event: error` / 502 `llm_invalid_output` |
| `POST /v1/ingest` без `X-Admin-Token` | 401 |
| `POST /v1/ingest` с неверным токеном | 401 |

## 9. Критерии приёмки MVP

- [ ] `/v1/health` 200, БД/Redis/Stockfish `ok`.
- [ ] `/v1/analyze` на 53 полуходах ≤ 30 с.
- [ ] Доска, ходы, оценки и мотивы отображаются.
- [ ] Комментарий генерируется и валидирован.
- [ ] SSE-комментарий и чат печатаются постепенно, `done` приходит.
- [ ] PATCH сохраняет правку и создаёт `edits`.
- [ ] Чат отвечает по фактам и не пропускает нелегальные ходы.
- [ ] `GET /v1/games/{id}` отдаёт `analysis_level` и `commentaries`.
- [ ] Production-образ собирается, SPA открывается, порты данных loopback.
- [ ] `ruff`, `mypy`, `pytest`, `pnpm typecheck`, `pnpm test`,
      `pnpm build` — зелёные.
