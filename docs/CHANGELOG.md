# Changelog

Все значимые изменения проекта ChessAnnotator.
Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **`POST /v1/analyze` (T-009):** PGN + `level` → структурированный анализ
  (moves, evals, классификация) с сохранением в `games`/`moves` и
  `analysis_id` в ответе; партии с FEN-стартом поддерживаются через
  PGN-заголовки `[SetUp]`/`[FEN]`.
- **`EnginePool` (T-009, KI-014 закрыт):** 4 параллельных процесса
  Stockfish (`ENGINE_POOL_SIZE`, до 8), очередь свободных, порядок
  результатов сохраняется; SLA на 53 полухода — 14.6 с (лимит 30 с).
- **Глобальный обработчик доменных ошибок:** `AppError` →
  `{"detail", "code"}` с кодами 400/422/500/504; ошибки движка отдают
  `Engine unavailable`.
- **Rate limiting:** Redis `INCR`+`EXPIRE`, 10 запросов/мин на IP для
  `/v1/analyze`, `Retry-After`/`X-RateLimit-*`, fail-open при недоступном
  Redis (KI-015).
- **Lifespan приложения:** пул движков и Redis-клиент создаются/закрываются
  с приложением; `create_app(settings)` для тестовой конфигурации.
- **Тесты:** `tests/unit/test_engine_pool.py` (7),
  `tests/unit/test_rate_limit.py` (7),
  `tests/integration/test_api_analyze.py` (4, включая SLA и проверку БД).

- **Engine Service (T-008):** пакет `app/engine/` — async-обёртка над
  Stockfish: `EngineService.analyze()` (MultiPV, параметры по уровню из
  SPEC §2.2), `classify_move()` (best/good/inaccuracy/mistake/blunder),
  `analyze_game_moves()` (оценка «до/после» для каждого полухода,
  `delta_cp`, SAN/UCI, мат как `±(10000 − 100·N)`); собственный
  асинхронный UCI-клиент с таймаутами и перезапуском процесса.
- **Ошибки движка:** `EngineError`, `EngineNotFoundError`,
  `EngineTimeoutError`, `InvalidLevelError` в `app/errors.py`.
- **`ParsedGame.initial_fen`:** стартовая позиция партии (для партий с
  FEN-стартом); используется движком при анализе истории ходов.
- **Тесты движка:** `tests/unit/test_engine.py` — 27 тестов, включая
  интеграцию с реальным Stockfish (MultiPV, бенчмарк «клуба», первые
  3 хода партии, легальность лучшего хода).
- **Ingestion (T-007):** пакет `app/ingestion/` — `parse_pgn()` (PGN →
  `ParsedGame`), `parse_fen()` (валидация FEN), `find_position_repeats()`
  (повторы позиций); ECO-код из заголовка `[ECO]` с fallback на
  курируемую таблицу дебютных префиксов; разметка фаз
  (дебют ≤ 30 полуходов, эндшпиль по материалу `< 2000 cp` или `< 10`
  фигур).
- **Доменные ошибки ingestion:** `AppError`, `InvalidPgnError`,
  `InvalidFenError` (`code`, `http_status` для будущего API-слоя).
- **Модели результата ingestion:** `ParsedMove`, `GameMetadata`,
  `PhaseBreakdown`, `PositionRepeat`, `ParsedGame` (Pydantic v2, `frozen`,
  `extra="forbid"`).
- **Тесты ingestion:** `tests/unit/test_ingestion.py` — 33 теста
  (10 входных PGN, FEN после каждого полухода, флаги взятия/шаха, фазы,
  ошибки, ECO, повторы), `xfail`-маркер KI-012.
- **Фикстура:** `tests/fixtures/test_game.pgn` (Test White / Test Black)
  и таблица инвариантов в `tests/fixtures/README.md`.
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
- **Тесты схемы (T-004/T-005):** `tests/test_health.py`,
  `tests/test_models.py` — 5 тестов, включая интеграционный тест схемы
  (defaults, каскады).
- **Документация:** `docs/development/RULES.md` (правила кода, тестов,
  коммитов, безопасности, окружения), `PROMPT_CURRENT.md`,
  `RELEASES.md`, `docs/CHANGELOG.md`, `docs/KNOWN_ISSUES.md`,
  `docs/README.md` (навигация), пошаговые отчёты в
  `docs/development/reports/`.
- **CI:** шаги `alembic check` и roundtrip-миграции
  (`downgrade base` → `upgrade head`) — DoD T-004.

### Changed

- `ruff`: `RUF001` добавлено в `ignore` вместе с `RUF002/RUF003` —
  сообщения и докстринги проекта на русском.
- Комментарий к `games.phase_breakdown` в `app/db/models.py` приведён к
  фактическому формату `{"opening_end_ply": …, "endgame_start_ply": …}`.
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

### Changed

- Ошибки движка в HTTP теперь отдают **500** (`EngineError`,
  `EngineNotFoundError`) и **504** (`EngineTimeoutError`) вместо 503 —
  как требует контракт T-009; текст для пользователя — `Engine unavailable`.
- `create_app()` принимает `Settings`, а создание пула движков и
  Redis-клиента перенесено в lifespan (корректное закрытие ресурсов).

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
