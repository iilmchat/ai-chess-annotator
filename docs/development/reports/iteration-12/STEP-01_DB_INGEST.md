# STEP-01 — Сброс БД и ingest партий (итерация 12)

**Дата:** 2026-10-28
**Итерация:** 12
**Задачи:** T-021b (подготовка), T-022 (подготовка), KI-029
**Статус:** ✅

## Что сделано

1. Остановлен проект и удалены тома: `docker compose down -v` (удалены
   `ai-chess-annotator_pg_data`, `ai-chess-annotator_redis_data`, сеть).
2. Подняты только БД и кэш: `docker compose up -d postgres redis`;
   дождались `healthy` у обоих контейнеров.
3. Применена миграция: `alembic upgrade head` → `b802598c3778 (head)`.
   `alembic check` — «No new upgrade operations detected».
4. Создан CLI `scripts/ingest_pgn.py` (`--file`, `--limit`, `--source`):
   в checkout скрипта не было (KI-027). В `ingest_pgn_text` добавлен
   параметр `limit`, чтобы API и CLI использовали один пайплайн.
5. Загружены три файла (`--source my-collection`, `--limit 50`):
   - `data/sample.pgn` → 10 партий (ID 1..10)
   - `data/sample1.pgn` → 42 партии (ID 11..52)
   - `data/sample2.pgn` → 50 партий (ID 53..102)
6. Проверка БД: 102 записи, 99 с непустым `annotation_text`, 0 записей с
   `embedding IS NULL`.

## Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Тома удалены | `docker compose down -v` | `Volume ... Removed` ×2, `Network ... Removed` |
| Сервисы здоровы | `docker inspect --format '{{.State.Health.Status}}'` | `postgres=healthy`, `redis=healthy` |
| Миграции | `alembic upgrade head` | `Running upgrade -> b802598c3778, init schema` |
| Нет дрейфа | `alembic check` | `No new upgrade operations detected.` |
| sample.pgn | `python scripts/ingest_pgn.py ... --limit 50` | `Загружено партий: 10` |
| sample1.pgn | то же | `Загружено партий: 42` |
| sample2.pgn | то же | `Загружено партий: 50` |
| Итог в БД | `COUNT(*)` по `annotated_games` | `total 102`, `with_ann 99`, `null_emb 0` |

## Файлы

| Файл | Что |
| :--- | :--- |
| `scripts/ingest_pgn.py` | новый CLI: argparse, UTF-8/cp1251, `SessionLocal`, commit, `engine.dispose()` |
| `app/rag/service.py` | `ingest_pgn_text(..., limit: int \| None)` с валидацией `limit ≥ 1` |

## Проблемы и отклонения

- **KI-029 (закрыт).** `password authentication failed for user "chess"`:
  старый том PostgreSQL сохранял прежний пароль, ENV применяется только к
  пустому `PGDATA`. Полный сброс решил проблему; доказательство — 102
  успешных ingest-а.
- **KI-027 (открыт).** `scripts/ingest_pgn.py` и коммиты итераций 10–11
  отсутствуют в checkout. Скрипт написан заново в рамках этой итерации.
- Эмбеддинги остаются хеш-заглушкой (KI-025) — на ingest это не влияет.

## Обоснование решений

- `limit` применён после `parse_pgn_games`, а не через «прочитать первые
  N байт»: PGN-партии не гарантируют одинаковый размер, а парсинг всего
  файла уже отлажен и покрыт тестами.
- Скрипт использует тот же `ingest_pgn_text`, что `POST /v1/ingest`:
  дублирование логики запрещено (RULES §1).
- Чтение файла вынесено из async-функции в `main` — `ASYNC240` и лишний
  blocking-вызов в event loop.

## Что дальше

T-021b: `PATCH /v1/commentaries/{id}` и запись в `edits`.
