# STEP-04 — Модели БД и первая миграция (T-004)

**Дата:** 2026-10-28
**Итерация:** 1 — Bootstrap (T-001…T-006)
**Статус:** ✅ выполнен

---

## 1. Что сделано

1. `app/db/models.py` — 8 таблиц из SPEC.md §4 на SQLAlchemy 2.0
   (`Mapped[...]` + `mapped_column`).
2. `app/db/base.py` — `Base` с `naming_convention` (предсказуемые имена
   `pk_*`, `uq_*`, `fk_*`, `ix_*` — их же использует Alembic).
3. `alembic/env.py` — `from app.db import models  # noqa: F401`, чтобы
   `--autogenerate` видел `Base.metadata`.
4. `alembic revision --autogenerate -m "init schema"` →
   `alembic/versions/b802598c3778_init_schema.py`, затем ручная доводка
   (см. § 3).
5. `tests/test_models.py` — unit-тесты метаданных + интеграционный тест
   на живой БД (defaults, каскады).
6. CI: добавлены шаги `alembic check` (дрейф моделей) и
   `alembic downgrade base && alembic upgrade head` (roundtrip-тест
   миграции, DoD T-004).

## 2. Схема

| Таблица | Колонки | Особенности |
| :--- | :--- | :--- |
| `users` | id, email, level, created_at | `level` — нативный enum `beginner\|club\|gm`, default `club`; `uq_users_email` |
| `games` | id, user_id, pgn, eco_code, phase_breakdown, created_at | FK → `users` `ON DELETE SET NULL` (гостевая партия без пользователя), `phase_breakdown` JSONB |
| `moves` | id, game_id, ply, san, fen, eval_cp, eval_mate, best_move_san, delta_cp, classification, engine_depth, multipv | FK → `games` `CASCADE`, индекс `(game_id, ply)`, `multipv` JSONB, `engine_depth` SMALLINT |
| `motifs` | id, move_id, type, description, importance | FK → `moves` `CASCADE`, `importance` SMALLINT default 0 |
| `annotated_games` | id, source, pgn, white, black, event, year, annotation_text, embedding | `embedding VECTOR(1024)` + **ivfflat** `vector_cosine_ops` (`lists=100`) |
| `retrieval_cache` | id, fen_hash, top_k_ids, created_at | `fen_hash` VARCHAR(64) + индекс, `top_k_ids BIGINT[]` |
| `commentaries` | id, game_id, level, text, model, tokens_in, tokens_out, cost_usd, validated, human_edited, created_at | FK → `games` `CASCADE`, `level` enum, `cost_usd NUMERIC(12,6)`, `validated`/`human_edited` default false |
| `edits` | id, commentary_id, original_text, edited_text, editor_id, created_at | FK → `commentaries` `CASCADE`, FK → `users` `SET NULL` |

Проверено `information_schema`: `users:4`, `games:6`, `moves:12`,
`motifs:5`, `annotated_games:9`, `retrieval_cache:4`, `commentaries:11`,
`edits:6` — совпадает с SPEC.md §4.

## 3. Ручные правки после autogenerate

1. `import pgvector` — Alembic рендерит тип как
   `pgvector.sqlalchemy.vector.VECTOR(dim=1024)`, без импорта миграция
   падает на `NameError`.
2. `CREATE EXTENSION IF NOT EXISTS vector` в начале `upgrade()`: таблица
   с `VECTOR` не создастся, пока расширения нет. В образе
   `pgvector/pgvector:pg17` расширение есть (0.8.7), но не активировано.
3. Нативный тип `level`: `postgresql.ENUM(..., create_type=False)` +
   явные `level_enum.create()/drop()`. Autogenerate не удаляет тип в
   `downgrade`, из-за чего после `downgrade base` оставался «висячий»
   `level` и повторный `upgrade` шёл на грабли `type "level" already
   exists`.
4. В `downgrade` расширение `vector` **не** удаляется (KI-003).

## 4. Проверки DoD

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| upgrade | `alembic upgrade head` | `Running upgrade -> b802598c3778`, exit 0 |
| current | `alembic current` | `b802598c3778 (head)` |
| 8 таблиц в БД | `psql -c "\dt"` | 9 строк = 8 таблиц + `alembic_version` |
| downgrade | `alembic downgrade base` | exit 0, осталась только `alembic_version`, тип `level` удалён |
| повторный upgrade | `alembic upgrade head` | exit 0 (roundtrip чистый) |
| нет дрейфа | `alembic check` | `No new upgrade operations detected.` |
| ivfflat | `pg_indexes` | `CREATE INDEX … USING ivfflat (embedding vector_cosine_ops) WITH (lists='100')` |
| расширение | `pg_extension` | `vector 0.8.7` |
| enum | `pg_enum` | `beginner, club, gm` |
| тесты | `pytest -q` | 5 passed (2 health + 3 schema) |

## 5. Решения и почему

1. **BIGINT + `GENERATED ALWAYS AS IDENTITY`** вместо `SERIAL` — SQL-стандарт,
   нет «висячих» последовательностей, id нельзя перезаписать вручную.
2. **`games.user_id` nullable + `SET NULL`** — `/v1/analyze` по SPEC работает
   без аутентификации (JWT появится позже), партия может быть анонимной;
   удаление пользователя не должно уносить его партии (GDPR-удаление
   пользователя — отдельный сценарий с явной чисткой).
3. **`level` — нативный PG-enum, а не VARCHAR+CHECK.** Значения заданы
   SPEC и вряд ли расширятся; БД сама отбрасывает мусор. Минус — добавление
   значения требует миграции (`ALTER TYPE … ADD VALUE`), это приемлемо.
4. **`classification` — VARCHAR(20), не enum.** Набор классов
   (blunder/mistake/inaccuracy/good/best/brilliant) ещё будет уточняться в
   T-008; жёсткий enum заставил бы мигрировать схему ради лабораторной
   строки. TODO для T-008: либо CHECK, либо enum.
5. **ivfflat `lists=100`** — стартовое значение для 100–200 партий
   (T-017); правило «lists ≈ rows/1000» применим при росте базы. Индекс
   создаётся пустым — pgvector это допускает, и он будет использоваться
   после `ANALYZE`/вставок.
6. **`server_default` вместо Python-default** для `level`, `importance`,
   `validated`, `human_edited`, `created_at` — значения должны быть
   корректны и при вставке «сырым» SQL (ingest-скрипты T-017).
7. **`asyncio`-драйвер только (asyncpg)** — Alembic уже настроен на
   `async_engine_from_config`; второй sync-драйвер (psycopg) не нужен.
8. **CI-roundtrip миграции** — дешёвая гарантия T-004 DoD: ловит
   «висячие» типы/индексы, которые не видит `alembic check`.

## 6. Что не сделано (осознанно)

- Нет `updated_at` — не требуется SPEC; добавим, когда появится
  редактирование сущностей.
- Нет `moves(game_id, ply) UNIQUE` — SPEC требует только индекс; вставка
  дублей ply остаётся ответственностью ingestion (T-007).
- Нет теста на конкурентные вставки/уникальность email — покрывается
  интеграционным тестом при появлении auth (Фаза 5).

## 7. Что дальше

Шаг 5 — `ruff`, `mypy`, `pytest` (формальная фиксация зелёного прогона).
