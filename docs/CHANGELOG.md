# Changelog

Все значимые изменения проекта ChessAnnotator.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Ingestion (T-007):** пакет `app/ingestion/` — `parse_pgn()` (PGN →
  `ParsedGame`), `parse_fen()` (валидация FEN), `find_position_repeats()`
  (повторы позиций); ECO-код из заголовка `[ECO]` с fallback на
  курируемую таблицу дебютных префиксов; разметка фаз
  (дебют ≤ 30 полуходов, эндшпиль по материалу `< 2000 cp` или `< 10`
  фигур).
- **Доменные ошибки:** `app/errors.py` — `AppError`, `InvalidPgnError`,
  `InvalidFenError` (`code`, `http_status` для будущего API-слоя).
- **Модели результата:** `ParsedMove`, `GameMetadata`, `PhaseBreakdown`,
  `PositionRepeat`, `ParsedGame` (Pydantic v2, `frozen`, `extra="forbid"`).
- **Тесты:** `tests/unit/test_ingestion.py` — 33 теста (10 входных PGN,
  FEN после каждого полухода, флаги взятия/шаха, фазы, ошибки, ECO,
  повторы), `xfail`-маркер KI-012.
- **Фикстура:** `tests/fixtures/test_game.pgn` (Test White / Test Black)
  и таблица инвариантов в `tests/fixtures/README.md`.
- **Документация:** отчёты `docs/development/reports/iteration-2/`,
  раздел «Итерация 2» в `RELEASES.md`, KI-010…KI-012 в
  `KNOWN_ISSUES.md`.

### Changed

- `ruff`: `RUF001` добавлено в `ignore` вместе с `RUF002/RUF003` —
  сообщения и докстринги проекта на русском.
- Комментарий к `games.phase_breakdown` в `app/db/models.py` приведён к
  фактическому формату `{"opening_end_ply": …, "endgame_start_ply": …}`.

- **Bootstrap проекта (T-001…T-006):** FastAPI-скелет (`app/main.py`,
  `app/config.py`), healthcheck `/v1/health` с проверкой БД, Redis,
  Stockfish и LLM, Docker Compose (PostgreSQL 17 + pgvector, Redis,
  опциональные Stockfish и vLLM), CI (ruff, mypy, alembic, pytest,
  typecheck, build), фронтенд-скелет (React 19 + Vite).
- **Схема БД (T-004):** 8 таблиц по SPEC.md §4 — `users`, `games`,
  `moves`, `motifs`, `annotated_games`, `retrieval_cache`,
  `commentaries`, `edits`; нативный enum `level`, `VECTOR(1024)` с
  ivfflat-индексом, индексы `moves(game_id, ply)` и
  `retrieval_cache(fen_hash)`.
- **Первая миграция** `b802598c3778_init_schema` (upgrade / downgrade /
  повторный upgrade проверены), `alembic check` без дрейфа.
- **Тесты (T-004/T-005):** `tests/test_health.py`,
  `tests/test_models.py` — 5 тестов, включая интеграционный тест схемы
  (defaults, каскады).
- **Документация:** `docs/development/RULES.md` (правила кода, тестов,
  коммитов, безопасности, окружения), `docs/development/PROMPT_CURRENT.md`,
  `docs/development/RELEASES.md`, `docs/CHANGELOG.md`,
  `docs/KNOWN_ISSUES.md`, `docs/README.md` (навигация), отчёты по шагам
  итерации в `docs/development/reports/iteration-1/`.
- **CI:** шаги `alembic check` и roundtrip-миграции
  (`downgrade base` → `upgrade head`) — DoD T-004.

### Changed

- `POSTGRES_HOST` / `REDIS_URL` по умолчанию — `127.0.0.1` вместо
  `localhost`: на Windows `localhost` резолвится в `::1`, а compose
  публикует порты только на IPv4-loopback (иначе healthcheck Redis
  падал по таймауту).
- HTTP-порт Redis в compose сделан переопределяемым: `REDIS_PORT`
  (по умолчанию 6380).
- `/v1/health` возвращает `degraded`, если хоть один компонент не `ok`
  (в том числе `skipped` для ненастроенной LLM) — как требует контракт.
- `pyproject.toml`: добавлен `pgvector`; убран `types-redis` (redis 5.x
  поставляет `py.typed`); отключены `RUF002/RUF003` (русские докстринги);
  добавлен `asyncio_default_fixture_loop_scope`.
- `web/pnpm-workspace.yaml`: `allowBuilds: esbuild` — pnpm 12 не выполняет
  postinstall-скрипты зависимостей без явного разрешения.

### Fixed

- Alembic-миграция доведена руками: `import pgvector`,
  `CREATE EXTENSION vector`, явные `create/drop` нативного enum `level`
  (иначе `downgrade base` + `upgrade` падали на существующем типе).
- `mypy`: конфликт имени `text` (колонка `commentaries.text` против
  `sqlalchemy.text`), устаревшие стабы `types-redis`.
- `ruff`: неиспользуемый импорт `Field`, `AsyncIterator` из `typing`,
  бесполезные `noqa`.

## [0.1.0] — 2026-10-28

- Первый релиз (MVP) — будет проставлен на T-027. Содержимое секции
  заполняется по итогам итераций 1–6.
