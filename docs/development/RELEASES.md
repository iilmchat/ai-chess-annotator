# Отчёты итераций — ChessAnnotator

Журнал итераций. Правила заполнения — `docs/development/RULES.md` §6.3.
Пошаговые детали каждой итерации — в `docs/development/reports/iteration-N/`.

---

## Шаблон отчёта итерации

```markdown
## Итерация N — <название> (T-XXX…T-YYY)

**Дата:** YYYY-MM-DD
**Статус:** ✅ завершена / ⚠️ частично / ❌ заблокирована

### Что сделано
- [x] T-XXX … (одна строка на задачу, с ключевым артефактом)

### Что не сделано и почему
- (пусто / ссылки на KI-NNN)

### Метрики
- Backend-тесты: N passed, M skipped
- Frontend-тесты: N passed
- CI: зелёный / красный (причина)
- Время итерации: X ч

### Что дальше
- T-YYY …
```

---

## Итерация 1 — Bootstrap (T-001…T-006)

**Дата:** 2026-10-28
**Статус:** ✅ завершена (с зафиксированными отклонениями окружения —
KI-001, KI-002, KI-006; они не блокируют продукт)
**Отчёты шагов:** `docs/development/reports/iteration-1/STEP-01…STEP-11`

### Что сделано

- [x] **T-001. Инициализация репозитория.** `git init`, ветка `main`,
       базовый коммит `bb3b7ce` (35 файлов), `.gitignore` покрывает
       `.env`, `.venv`, `node_modules`, `web/dist`.
- [x] **T-002. Docker Compose (dev).** `postgres` (pgvector/pgvector:pg17)
       и `redis` (redis:7-alpine) — оба `healthy`, порты только на
       loopback: `5434` и `6381` (6380 занят чужим контейнером — KI-001).
       Профили `engine` (Stockfish-over-TCP) и `llm-local` (vLLM) не
       поднимались — опциональны.
- [x] **T-003. FastAPI + healthcheck.** `GET /v1/health` → `degraded`,
       `db/redis/stockfish = ok`, `llm = skipped`; unit-тест на контракт;
       живая проверка через uvicorn (порт 8010 — KI-002).
- [x] **T-004. SQLAlchemy + Alembic.** 8 таблиц из SPEC §4
       (`app/db/models.py`), миграция `b802598c3778_init_schema`,
       `upgrade` / `downgrade base` / повторный `upgrade` — чистые,
       `alembic check` — без дрейфа; 3 теста схемы (включая интеграционный
       на defaults и каскады); в CI добавлены `alembic check` и
       roundtrip-шаг.
- [x] **T-005. CI (GitHub Actions).** `ci.yml` обновлён: ruff, mypy,
       alembic upgrade/check/roundtrip, pytest; frontend — typecheck,
       build, test. Локально все шаги зелёные; прогон в Actions — после
       push (см. «Метрики»).
- [x] **T-006. Базовые документы.** `RULES.md` (дополнен §11 «Окружение»
       и §12 «Отчёты по шагам»), `PROMPT_CURRENT.md`, `RELEASES.md` (этот
       файл), `docs/CHANGELOG.md`, `docs/KNOWN_ISSUES.md` (KI-001…KI-009),
       `docs/README.md` (навигация) + 11 отчётов по шагам.

Дополнительно к задачам (в рамках DoD):

- `tests/fixtures/example.pgn` — партия Гукеш — Каймер (Прага 2024),
  проверена `python-chess` (27 полуходов, результат `1-0`).
- `web/pnpm-workspace.yaml` — разрешение `esbuild` для pnpm 12.
- `pyproject.toml` — `pgvector`, снятие `types-redis`, конфиг pytest-asyncio.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Прогон CI в GitHub Actions | Локальная машина, нет push в remote; конфигурация проверена пошагово вручную (`ruff`, `mypy`, `pytest`, `alembic`, `typecheck`, `build`) | — |
| Фронтенд-тесты (0) | Нет компонентов до T-019 | KI-008 |
| Документы `ARCHITECTURE.md`, `USER_GUIDE.md`, `ASSISTANT_LEVELS.md`, `MANUAL_TESTING_GUIDE.md`, `v0.1/DESIGN_V010.md` | Относятся к T-026 / T-017 | KI-009 |
| Канонические порты 6380/8000 на этой машине | Заняты чужими контейнерами | KI-001, KI-002 |

### Метрики

- **Backend-тесты:** 5 passed, 0 skipped (2 файла: `test_health.py`,
  `test_models.py`).
- **Frontend-тесты:** 0 passed (0 файлов; CI использует
  `--passWithNoTests`, см. KI-008).
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, 10 файлов.
- **Миграции:** 1 ревизия `b802598c3778`; 8 таблиц + `alembic_version`;
  roundtrip и `alembic check` — зелёные.
- **Healthcheck:** `{"status":"degraded","components":{"db":{"status":"ok"},
  "redis":{"status":"ok"},"stockfish":{"status":"ok"},"llm":{"status":"skipped"}}}`
  (фактический ответ — `reports/iteration-1/STEP-06_HEALTHCHECK.md`).
- **CI:** конфигурация обновлена; в Actions не запускался (нет push).
- **Время итерации:** ≈ 0.4 ч (01:24–01:50 MSK), 11 шагов.

### Что дальше

- **T-007. Ingestion:** `app/ingestion/` на `python-chess` — парсинг
  PGN/FEN, валидация, ECO-код, детект транспозиций, разметка фаз;
  10 тестовых PGN (включая `tests/fixtures/example.pgn`).
- Перед T-007: закрыть/подтвердить KI-006 (скрипт установки Stockfish)
  — понадобится в T-008, не в T-007.

---

## Итерация 2 — Ingestion (T-007)

**Дата:** 2026-10-28
**Статус:** ✅ завершена (с открытыми KI-010…KI-012, ни один не блокирует T-008)
**Отчёты шагов:** `docs/development/reports/iteration-2/STEP-01…STEP-05`

### Что сделано

- [x] **T-007. Ingestion: PGN/FEN → нормализованная партия.**
      Пакет `app/ingestion/`:
      - `models.py` — `ParsedMove`, `GameMetadata`, `PhaseBreakdown`,
        `PositionRepeat`, `ParsedGame` (frozen, `extra="forbid"`, ECO-паттерн
        на уровне модели);
      - `parser.py` — `parse_pgn`, `parse_fen`, `find_position_repeats`;
        пороги фаз (`OPENING_MAX_PLY=30`, материал `< 2000 cp` или
        `< 10` фигур), перевод ошибок `python-chess` в доменные;
      - `eco.py` — нормализация заголовка `[ECO]` + курируемая таблица
        префиксов (самое длинное совпадение);
      - `__init__.py` — публичный контракт пакета.
- [x] **`app/errors.py`** — `AppError` / `InvalidPgnError` /
      `InvalidFenError` с `code` и `http_status` (задел под T-009).
- [x] **Тесты:** `tests/unit/test_ingestion.py` — 33 теста, включая
      10 входных PGN (2 фикстуры + 8 дебютов), FEN-инвариант для каждого
      полухода, флаги взятия/шаха, разметку фаз, все ветки ошибок и
      `xfail`-маркер KI-012.
- [x] **Фикстура** `tests/fixtures/test_game.pgn` (Test White / Test Black,
      без `[ECO]`) + обновлённый `tests/fixtures/README.md` с инвариантами.
- [x] **Документация:** отчёты `reports/iteration-2/`, этот отчёт,
      `docs/CHANGELOG.md`, `docs/KNOWN_ISSUES.md` (KI-010…KI-012),
      `README.md`, `docs/README.md`, `docs/development/PROMPT_CURRENT.md`;
      комментарий к `games.phase_breakdown` в `app/db/models.py` приведён
      к фактическому формату.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Полная база ECO и транспозиции «относительно теории» | `python-chess` не поставляет ECO/opening book; нужен внешний источник (полиглот-книга) | KI-011 (T-017) |
| Строгий контроль «PGN прочитан до конца» | `python-chess` молча обрывает разбор на неизвестном токене | KI-012 (T-017 или раньше) |
| Перенос плоских тестов итерации 1 в `tests/unit|integration` | Не блокирует T-007, требует аккуратного разделения | KI-010 (T-008) |
| API-эндпоинт и запись в БД | Это T-009 (требует T-008) | — |

### Метрики

- **Backend-тесты:** **38 passed, 1 xfailed** (3 файла; из них 33 —
  ingestion). `xfailed` — осознанный маркер KI-012 (`strict=True`).
- **Frontend-тесты:** 0 (без изменений, KI-008).
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, **15** файлов
  (было 10: +`errors.py` и 4 файла `app/ingestion/`).
- **Покрытие ingestion:** 10 PGN-входов, все проигрываются без ошибок;
  FEN после каждого из 53 полуходов тестовой партии сверен с независимым
  `push_san`.
- **Производительность:** разбор партии из 53 полуходов — единицы
  миллисекунд (юнит-тесты целиком 1.2 с, включая импорт `python-chess`).
- **CI:** конфигурация не менялась; в Actions не запускался (нет push).
- **Время итерации:** ≈ 0.2 ч (01:46–01:55 MSK), 5 шагов.

### Что дальше

- **T-008. Engine Service:** `app/engine/` — async-обёртка над Stockfish
  (UCI), MultiPV, глубина по уровню, классификация ходов
  (blunder/mistake/inaccuracy/good/best/brilliant), бенчмарк ≤ 1 с/ход
  для «клуба».
- Перед T-008: понадобится KI-006 (путь к Stockfish) — на этой машине
  бинарник уже стоит (`D:\Tools\stockfish\stockfish.exe`,
  `STOCKFISH_PATH` в `.env`).

---

## Итерация 3 — Engine Service (T-008)

**Дата:** 2026-10-28
**Статус:** ✅ завершена (с открытыми KI-013, KI-014; оба — про транспорт и
производительность, не про корректность)
**Отчёты шагов:** `docs/development/reports/iteration-3/STEP-01…STEP-04`

### Что сделано

- [x] **T-008. Engine Service: Stockfish-обёртка.** Пакет `app/engine/`:
      - `models.py` — `EngineLine` (multipv, depth, score_cp/mate, move_uci,
        pv; валидатор «ровно одна оценка»), `MoveAnalysis`
        (оценки приведены к точке зрения сделавшего ход, `delta_cp ≥ 0`,
        `classification` как `Literal`);
      - `service.py` — `EngineService`:
        `analyze()` (MultiPV, параметры по уровню из SPEC §2.2),
        `classify_move()` (пороги best/good/inaccuracy/mistake/blunder),
        `analyze_game_moves()` (N+1 поисков, сверка истории с FEN-ами,
        SAN/UCI-конверсия, кодирование мата `±(10000 − 100·N)`),
        `aclose()`/async-context-manager;
      - собственный **async UCI-клиент** (`_UciEngine`): один процесс на
        сервис, `asyncio.Lock`, таймауты (`movetime` + запас), перезапуск
        процесса после зависания.
- [x] **Ошибки движка** в `app/errors.py`: `EngineError`,
      `EngineNotFoundError` (нет бинарника, KI-006), `EngineTimeoutError`,
      `InvalidLevelError`.
- [x] **`ParsedGame.initial_fen`** добавлен в ingestion: без него нельзя
      корректно проанализировать партию с FEN-стартом (`analyze_game_moves`
      принимает `initial_fen`, по умолчанию — начальная расстановка).
- [x] **Тесты:** `tests/unit/test_engine.py` — 27 тестов: границы
      классификации, профили уровней, модели, контракт ошибок без запуска
      процесса, интеграция с реальным Stockfish (поиск бинарника,
      MultiPV-линии, бенчмарк «клуба», первые 3 хода партии, легальность
      лучшего хода, согласованность `delta_cp`).
- [x] **Документация:** отчёты `reports/iteration-3/`, этот отчёт,
      `CHANGELOG.md`, KI-013/KI-014 в `KNOWN_ISSUES.md`, `README.md`,
      `docs/README.md`, `PROMPT_CURRENT.md`, `TODO.md`.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| TCP-транспорт движка (`STOCKFISH_HOST`, docker-профиль `engine`) | Реализован только локальный процесс; TCP — отдельный транспорт | KI-013 |
| Укладывание полной партии в 30 с на «клубе» | N+1 поисков по ~1 с; нужен пул движков/кэш/критические ходы | KI-014 (после T-009) |
| `brilliant` | Требует жертвы/единственности хода — это T-011 (Policy) | SPEC §2.2 |
| API-эндпоинт `/v1/analyze` | T-009 (следующая задача) | — |

### Метрики

- **Backend-тесты:** **65 passed, 1 xfailed** (4 файла; +27 движковых).
  Полный прогон — 7.06 с.
- **Бенчмарк `club`** (глубина 18, MultiPV 3, movetime 1000 мс), факт:
  **≈ 1004 мс** на стартовой позиции (движок достигает глубины 17 в
  пределах бюджета); тест допускает ≤ 1.15 с (1000 мс бюджета + IPC).
- **Бенчмарк `beginner`** (глубина 12, MultiPV 1): **≈ 131 мс**,
  глубина 12 достигается полностью.
- **`analyze_game_moves`**: 4 полухода на «новичке» — **≈ 501 мс**
  (5 поисков), т.е. один поиск ≈ 100 мс на этой машине.
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, **18** файлов
  (+`app/engine` 3 файла).
- **CI:** конфигурация не менялась; в Actions не запускался (нет push).
- **Время итерации:** ≈ 0.1 ч (01:56–02:01 MSK), 4 шага.

### Что дальше

- **T-009. `POST /v1/analyze`:** приём PGN/FEN + `level`, вызов ingestion →
  engine, сохранение партии/ходов в БД, `analysis_id` в ответе,
  rate limiting, маппинг `AppError` → HTTP.
- При проектировании T-009 учесть KI-014 (бюджет 30 с на 40 ходов) и
  KI-013 (движок в Docker).

---

## Итерация 4 — API `/v1/analyze` и пул движков (T-009)

**Дата:** 2026-10-28
**Статус:** ✅ завершена. **KI-014 закрыт** (SLA выполнен с 2-кратным запасом)
**Отчёты шагов:** `docs/development/reports/iteration-4/STEP-01…STEP-04`

### Что сделано

- [x] **KI-014 решён: `EnginePool`** (`app/engine/pool.py`) — 4 независимых
      процесса Stockfish (настраивается `ENGINE_POOL_SIZE`, максимум 8),
      очередь свободных (`asyncio.Queue`), параллельный анализ позиций
      через `asyncio.gather` с сохранением порядка. Общие шаги «история
      ходов → позиции» и «линии движка → `MoveAnalysis`» вынесены в
      `prepare_positions()` / `build_move_analyses()`, чтобы пул и
      последовательный `EngineService` считали одинаково.
- [x] **`POST /v1/analyze`** (`app/api/v1/analyze.py`): `{pgn, level}` →
      `{analysis_id, status, meta, moves_analyzed, time_taken_ms, summary}`;
      сохранение `games` + 53 × `moves` (san, fen, eval_cp/eval_mate,
      best_move_san, delta_cp, classification, engine_depth).
- [x] **Глобальный обработчик `AppError`** → `HTTP 400/422/500/504` с телом
      `{"detail", "code"}`; для ошибок движка `detail = "Engine unavailable"`.
- [x] **Rate limiting** (`app/api/ratelimit.py`): Redis `INCR`+`EXPIRE`,
      10 запросов/мин на IP для `POST /v1/analyze`, заголовки
      `Retry-After`/`X-RateLimit-*`, fail-open при недоступном Redis.
- [x] **Lifespan**: пул движков и Redis-клиент создаются на старте и
      закрываются на остановке; `create_app(settings)` принимает настройки
      (нужно тестам).
- [x] **Тесты:** `tests/unit/test_engine_pool.py` (7), 
      `tests/unit/test_rate_limit.py` (7), 
      `tests/integration/test_api_analyze.py` (4, включая SLA).
- [x] **Документация:** отчёты `reports/iteration-4/`, `CHANGELOG.md`,
      `KNOWN_ISSUES.md` (KI-014 → Closed, +KI-015…KI-018), `README.md`,
      `docs/README.md`, `PROMPT_CURRENT.md`.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Публичные UUID-идентификаторы анализа | В схеме SPEC §4 нет `public_id`; менять схему вне T-009 не стали | KI-016 |
| Гарантия SLA для партий 160+ полуходов | Время растёт линейно с длиной; бюджета запроса нет | KI-017 |
| Сохранение MultiPV-линий в `moves.multipv` | `MoveAnalysis` не несёт линии наружу; понадобятся в T-011/T-012 | KI-018 |
| TCP-режим движка | Не входил в T-009 | KI-013 |

### Метрики

- **SLA (KI-014):** `example.pgn` (53 полухода), уровень `club`, пул 4 —
  **14 646 мс** при лимите 30 000 мс (ответ API `time_taken_ms` = 14646,
  настенное время 14 660 мс). Прежняя оценка последовательного анализа —
  ≈ 41 с на 40 ходов, т.е. ускорение ≈ ×3.7.
- **Backend-тесты:** **83 passed, 1 xfailed** (7 файлов; +18 к итерации 3).
  Полный прогон — 26.28 с (из них 15 с — SLA-тест).
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, **21** файл.
- **Ошибки API:** битый PGN → 400 `invalid_pgn`, неизвестный уровень →
  422, движок недоступен → 500 `Engine unavailable`.
- **CI:** конфигурация не менялась; в Actions не запускался (нет push).
- **Время итерации:** ≈ 0.2 ч.

### Что дальше

- **T-010. Motif Detector:** `app/motifs/` — эвристики `python-chess`
  (слабые поля, проходные, открытые линии, безопасность короля) +
  фичи движка → структурированные мотивы, привязанные к ходам;
  recall ≥ 80% на 20 тестовых позициях.
- Учесть KI-018: мотивам и Policy нужны MultiPV-линии — расширить
  `MoveAnalysis`/`moves.multipv` в T-010/T-011.

---

## Итерация 5 — Motif Detector и Policy Layer (T-010, T-011)

**Дата:** 2026-10-28
**Статус:** ✅ завершена. **KI-018 закрыт** (PV-линии сохраняются;
отдельная миграция не понадобилась — колонка `moves.multipv` есть с T-004)
**Отчёты шагов:** `docs/development/reports/iteration-5/STEP-01…STEP-04`

### Что сделано

- [x] **T-010. Motif Detector.** Пакет `app/motifs/`:
      - `models.py` — `Motif` (`type` как `Literal`, `description`,
        `importance` 1…5);
      - `detector.py` — `detect_motifs(fen, multipv)`: проходные пешки,
        слабые поля у короля, открытые линии и большие диагонали,
        безопасность короля, вилка и связка по доске, мат/выигрыш материала
        по лучшей линии движка; дедупликация и лимит 6 мотивов на позицию.
- [x] **Интеграция мотивов.** `MoveAnalysis.motifs`, расчёт внутри
      `build_move_analyses` (после каждого хода), запись в таблицу `motifs`
      (`type`, `description`, `importance`, `move_id`) в той же транзакции,
      что `games`/`moves`.
- [x] **KI-018.** `MoveAnalysis.lines` (MultiPV позиции **до** хода) →
      `moves.multipv` JSONB. Проверено: 53/53 хода с непустым `multipv`
      (до 3 линий на уровне `club`), `multipv[0].pv` непуст.
- [x] **T-011. Policy Layer.** `app/engine/policy.py`:
      `choose_recommended_move(multipv, level, fen=...)` — `gm` → топ-1;
      `club` → топ-1, но при разнице < 20 cp отдаётся вторая линия;
      `beginner` → из top-3 отбрасываются «ранний ферзь» и ходы, оставляющие
      фигуру под боем; приоритет развитию/центру/рокировке; откат на топ-1.
- [x] **Тесты:** `tests/unit/test_motifs.py` (13), `tests/unit/test_policy.py`
      (15); интеграционный тест дополнен проверками `multipv` и `motifs`.
- [x] **Документация:** отчёты `reports/iteration-5/`, `CHANGELOG.md`,
      `KNOWN_ISSUES.md` (KI-018 → Closed, +KI-019, KI-020), `README.md`,
      `docs/README.md`, `PROMPT_CURRENT.md`, `TODO.md`.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Метрика recall мотивов ≥ 80% (DoD TODO T-010) | Нет размеченного эталона из 20 позиций; сделаны эвристики и тесты по типам | KI-019 |
| Policy «новичка» с защитой от ложных отбраковок жертв | Нет SEE/проверки мата после жертвы | KI-020 |
| Мотивы и recommended_move в ответе API | Это T-012 (следующая задача) | — |

### Метрики

- **Backend-тесты:** **111 passed, 1 xfailed** (9 файлов; +28 к итерации 4).
  Полный прогон — 24.16 с.
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, **25** файлов.
- **Мотивы на партии Гукеш — Каймер (54 позиции):** `weak_square` 45,
  `king_safety` 35, `passed_pawn` 10, `pin` 10, `open_file` 7, `fork` 2,
  `open_diagonal` 2.
- **KI-018:** 53 хода × 3 PV-линии сохранены; короткая партия через API —
  6 ходов × 3 линии, мотивы 12 строк (`king_safety`).
- **Policy (демо, позиция после 1.e4 e5):** движок советует `2.Qh5` —
  `gm` берёт `Qh5`, `club` и `beginner` — `Bc4` (развитие). В позиции, где
  топ-1 `Nxe5` теряет коня, `beginner` берёт `Bc4`, `gm` — `Nxe5`.
- **SLA не деградировал:** 53 полухода «клуб» по-прежнему ≈ 15 с (добавление
  мотивов и сохранения PV — доли секунды).
- **CI:** конфигурация не менялась; в Actions не запускался (нет push).
- **Время итерации:** ≈ 0.2 ч.

### Что дальше

- **T-012. Расширить `/v1/analyze` мотивами и policy:** добавить в ответ
  `motifs` по ходам и `recommended_move` под уровень (по
  `choose_recommended_move`), обновить OpenAPI-схему и тесты.
- При выдаче рекомендаций учесть KI-020 (жертвы) и KI-018 (линии уже есть).

---

## Итерация 6 — API с мотивами и Policy + LLM-адаптер (T-012, T-013)

**Дата:** 2026-10-28
**Статус:** ✅ завершена. **KI-020 закрыт**; живой тест LLM пройден на обоих
провайдерах (LM Studio и DeepSeek)
**Отчёты шагов:** `docs/development/reports/iteration-6/STEP-01…STEP-04`

### Что сделано

- [x] **T-012. `/v1/analyze` с мотивами и рекомендацией.** В ответ добавлен
      блок `moves`: по каждому полуходу `ply`, `san`, `uci`, `eval_cp`,
      `mate`, `best_move_san`, `delta_cp`, `classification`, `motifs`
      (список `Motif`) и `recommended_move` (`san`, `uci`, `eval_cp`,
      `mate`) — результат `choose_recommended_move` для позиции **до** хода
      с учётом уровня запроса.
- [x] **KI-020 закрыт:** линия с `EngineLine.mate > 0` (мат в свою пользу)
      возвращается Policy **до** любых фильтров — жертва, ведущая к мату,
      разрешена новичку всегда. Отрицательный мат фильтры не обходит.
- [x] **T-013. LLM-адаптер.** Пакет `app/llm/`:
      - `base.py` — `BaseLLMClient` (`generate`, `complete`, `aclose`),
        `LLMResult`, `LLMUsage`;
      - `openai_client.py` — `OpenAICompatibleClient` (LM Studio / DeepSeek /
        OpenAI): `httpx.AsyncClient`, таймаут, ретраи на 429/5xx с
        `Retry-After`, разбор `choices` и `usage`, логи без текста промпта;
      - `factory.py` — маршрутизация: `beginner`/`club` → LM Studio,
        `gm` → DeepSeek; ключ DeepSeek обязателен (`LLMNotConfiguredError`).
- [x] **Конфигурация:** `vllm_base_url=http://127.0.0.1:8034/v1`,
      `vllm_model=qwen/qwen3-4b-2507`, `vllm_api_key=lm-studio`,
      `deepseek_base_url=https://api.deepseek.com`, `deepseek_model=deepseek-chat`,
      `llm_max_tokens=512`, `llm_timeout_s=60`, ретраи.
- [x] **Ошибки:** `LLMError`, `LLMNotConfiguredError`, `LLMTimeoutError`
      (503/503/504).
- [x] **Тесты:** `tests/unit/test_llm.py` (13, моки `MockTransport`),
      `tests/integration/test_llm_live.py` (2, живые, со skip),
      +4 теста policy на KI-020 и различие рекомендаций beginner/gm.
      Интеграционный тест `/v1/analyze` проверяет `moves`/`motifs`/
      `recommended_move` для всех 53 ходов.
- [x] **Документация:** отчёты `reports/iteration-6/`, `CHANGELOG.md`,
      `KNOWN_ISSUES.md` (KI-020 → Closed, +KI-021, KI-022), `README.md`,
      `docs/README.md`, `PROMPT_CURRENT.md`, `.env.example`, `TODO.md`.

### Выбор локальной модели (важно)

`qwen3-8b` — thinking-модель: на этом хосте 120 токенов генерировались
**41 с**, 300 токенов — **157 с**, а без `max_tokens` запрос не уложился в
180 с (всё уходит в reasoning, `content` пустой). Модель
`qwen/qwen3-4b-2507` (non-thinking) отвечает за **0.9–1.3 с** (тёплый старт)
и возвращает нормальный текст. Поэтому локальный дефолт — `qwen/qwen3-4b-2507`;
`qwen3-8b` остаётся вариантом «умнее, но медленнее» (KI-022 фиксирует
холодный старт ~12 с).

### Живой тест (фактические результаты)

| Уровень | Провайдер | Модель | Токены (in/out) | Время | Ответ |
| :--- | :--- | :--- | :--- | ---: | :--- |
| `club` | LM Studio | `qwen/qwen3-4b-2507` | 21 / 14 | **1741 мс** | «Привет, шахматы! 😊♟️» |
| `gm` | DeepSeek | `deepseek-chat` → `deepseek-flash` | 16 / 8 | **1605 мс** | «Привет, шахматы!» |

Промпт: `Скажи 'Привет, шахматы!'`. Ключи в отчёт и логи не попадают.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Стоимость LLM (`cost_usd`) | Нет прайс-листа моделей в настройках | KI-021 |
| Прогрев локальной модели при старте | Появится вместе с эндпоинтом комментария | KI-022 / T-016 |
| Промпты 3 уровней и валидатор вариантов | Это T-014/T-015 (следующие задачи) | — |

### Метрики

- **Backend-тесты:** **130 passed, 1 xfailed** (11 файлов; +19 к итерации 5).
  Полный прогон — 31.05 с (включая 2 живых LLM-запроса).
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, **29** файлов.
- **SLA `/v1/analyze`** не изменился: 53 полухода «клуб» ≈ 15 с при лимите
  30 с (ответ стал больше: 53 × (мотивы + рекомендация)).
- **KI:** 22 записи, 19 Open, 3 Closed (KI-014, KI-018, KI-020).
- **Время итерации:** ≈ 0.3 ч.

### Что дальше

- **T-014. Системные промпты (3 уровня):** `app/prompts/{beginner,club,gm}.txt`
  + сборка «пакета фактов» из анализа (мотивы, рекомендации, линии).
- **T-015. Validator:** каждый вариант из LLM-ответа прогоняется через
  Stockfish; нелегальные/ошибочные отбрасываются.

---

## Итерация 7 — Промпты и валидатор (T-014, T-015)

**Дата:** 2026-10-28
**Статус:** ✅ завершена. **KI-022 закрыт** (прогрев LM Studio); живая
генерация комментария прошла валидатор
**Отчёты шагов:** `docs/development/reports/iteration-7/STEP-01…STEP-04`

### Что сделано

- [x] **T-014. Системные промпты.** `app/prompts/{beginner,club,gm}.txt`
      на основе SPEC §6, но строже: только факты из пакета, SAN без
      Markdown (`e4`, а не `**e4**`), фиксированная длина (150–450 слов),
      без списков/таблиц. Загрузчик `load_system_prompt(level)` с кешем и
      `InvalidLevelError` на неизвестном уровне.
- [x] **`build_fact_package(game, analyses, level)`** (`app/llm/prompts.py`):
      метаданные партии, все ходы в SAN, до 10 ключевых моментов
      (ошибки + мотивы важности ≥ 4, сортировка по потере), для каждого —
      оценка (за белых), класс, лучший ход, рекомендация Policy, PV в SAN,
      мотивы с важностью.
- [x] **Прогрев LLM (KI-022).** `app/llm/warmup.py` + фоновая задача в
      lifespan: короткий запрос в LM Studio при старте, best-effort,
      отменяется при остановке. Настройка `llm_warmup_enabled`.
- [x] **T-015. Валидатор.** `app/engine/validator.py`:
      `extract_moves_from_text` (SAN-регэксп: фигуры, пешки, взятия,
      превращения, шах/мат, рокировка в `0-0`-нотации), `validate_moves`
      (проигрывание на доске + проверка альтернатив по позициям партии),
      `validate_llm_response(text, initial_fen, engine_service)`.
- [x] **Тесты:** `tests/unit/test_prompts.py` (11),
      `tests/unit/test_validator.py` (14),
      `tests/unit/test_warmup.py` (3), живой E2E
      `tests/integration/test_generation.py` (1).
- [x] **Документация:** отчёты `reports/iteration-7/`, `CHANGELOG.md`,
      `KNOWN_ISSUES.md` (KI-022 → Closed, +KI-023, KI-024), `README.md`,
      `docs/README.md`, `PROMPT_CURRENT.md`, `TODO.md`.

### Живой прогон E2E (example.pgn, уровень club)

| Этап | Результат |
| :--- | :--- |
| Анализ движком (53 полухода) | 15 570 мс |
| Пакет фактов | 4 297 символов, 106 строк |
| Генерация LM Studio (`qwen/qwen3-4b-2507`) | 54 085 мс, 2759 / 494 токенов |
| Извлечено ходов из текста | 22 |
| Валидатор | **все легальны** (`illegal=[]`) |

Первые фразы сгенерированного комментария:

> Партия прошла в структуре ЭКО Е21 — дебют с пешкой d4 и развитием на c4,
> где белые сразу после 3. Nc3 играют Bg5, что создаёт давление на центр.
> Важным моментом стало развитие белых на f4 на полуходе 16, что движком
> оценено как лучший ход (класс best), с оценкой +108 cp. Это позволило
> белым активизировать пешку g6 и начать давление на d5, что в дальнейшем
> стало основой для проходной пешки. …Важным переломом стал ход 15… Rxe5 —
> движок оценивает его как inaccuracy, так как dxe5 было бы лучше, с потерей
> 96 cp.

(Полный текст — в отчёте `reports/iteration-7/STEP-03_LIVE_E2E.md`.)

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Проверка семантики (оценки/атрибуция) в тексте | Валидатор проверяет только легальность SAN | KI-023 |
| Быстрая генерация локальной моделью | ~9 ток/с на этом хосте (≈54 с на комментарий) | KI-024 |
| Эндпоинт `/v1/commentary` | Это T-016 | — |

### Метрики

- **Backend-тесты:** **158 passed, 1 xfailed** (15 файлов; +28 к итерации 6).
  Полный прогон — 88.56 с (включая живой E2E ≈ 70 с).
- **ruff:** 0 ошибок. **mypy (strict):** 0 ошибок, **33** файла.
- **Прогрев LLM:** `llm_warmup_ok elapsed_ms=934`, задача завершилась,
  остановка приложения чистая.
- **KI:** 24 записи, 20 Open, 4 Closed (KI-014, KI-018, KI-020, KI-022).
- **Время итерации:** ≈ 0.4 ч.

### Что дальше

- **T-016. `POST /v1/commentary`:** собрать факт-пакет, сгенерировать текст,
  провалидировать, сохранить в `commentaries` (с токенами и `validated`),
  вернуть клиенту; учесть KI-023 (семантика) и KI-024 (стриминг/фон).

---

## Итерация 8 — `POST /v1/commentary` (T-016)

**Дата:** 2026-10-28
**Статус:** ✅ завершена (живой прогон: 29/29 ходов легальны, `attempts=1`)
**Отчёты шагов:** `docs/development/reports/iteration-8/`

### Что сделано

- [x] **Сервис генерации** `app/commentary/service.py`: загрузка партии и
      анализа из БД (`moves` + `motifs` + `multipv` → `MoveAnalysis`),
      пакет фактов, LLM, валидатор с retry (до `commentary_max_retries`),
      поддержка `style`.
- [x] **`POST /v1/commentary`** (`app/api/v1/commentary.py`): запрос
      `{analysis_id, level, style?}`, ответ
      `{commentary_id, text, tokens_used, validated, model, attempts}`;
      запись в `commentaries` (текст, модель, токены, `validated`).
- [x] **Ошибки:** `NotFoundError` (404), `LLMValidationError` (502) —
      нелегальные ходы не сохраняются и не отдаются пользователю.
- [x] **Rate limiting** расширен на `POST /v1/commentary`.
- [x] **Фикс:** `engine.dispose()` в lifespan — устранён `500 Event loop is
      closed` при нескольких `TestClient` в одном процессе.
- [x] **Тесты:** `tests/integration/test_api_commentary.py` — 7 (6 быстрых +
      1 живой).

### Метрики

- **Backend-тесты:** **165 passed, 1 xfailed** (16 файлов; +7 к итерации 7).
  Полный прогон — 159.62 с.
- **Живой комментарий (club):** 52.4 с, 2733/570 токенов, `attempts=1`,
  валидатор: **29 ходов, все легальны**; запись `commentary_id=21` в БД.
- **ruff:** 0; **mypy (strict):** 0 ошибок, 36 файлов.
- **Время итерации:** ≈ 0.4 ч.

### Что дальше

- **T-017. RAG:** `POST /v1/ingest` (multipart PGN), эмбеддинги,
  `find_similar_positions`, RAG-контекст для `gm` в `/v1/commentary`.

---

## Итерация 9 — RAG и база аннотированных партий (T-017)

**Дата:** 2026-10-28
**Статус:** ✅ завершена (ingest → retrieval → RAG-контекст для gm)
**Отчёты шагов:** `docs/development/reports/iteration-9/`

### Что сделано

- [x] **Эмбеддер** `app/rag/embedder.py`: детерминированный
      `HashEmbedder` (1024 измерения, bag-of-words hashing, L2-нормализация)
      по нормализованному FEN. Без torch/sentence-transformers.
- [x] **RAG-сервис** `app/rag/service.py`: `parse_pgn_games`
      (несколько партий в одном файле), `ingest_pgn_text` (pgn, метаданные,
      аннотации из комментариев, год, эмбеддинг), `find_similar_positions`
      (косинусное расстояние через pgvector, `top_k`).
- [x] **`POST /v1/ingest`** `app/api/v1/ingest.py`: multipart-файл
      (до 5 МБ, UTF-8/cp1251), ответ `{ingested, ids}`, ошибки 400.
- [x] **RAG-контекст для `gm`:** `app/rag/context.py` +
      `app.state.rag_context_builder` в lifespan; в `/v1/commentary` для
      `gm` к пакету фактов добавляется раздел «Похожие позиции из базы
      аннотаций (RAG)» по 2–3 ключевым моментам. Для `club`/`beginner`
      RAG не вызывается. Сбой RAG не ломает генерацию.
- [x] **Тесты:** `tests/unit/test_rag.py` (6) — эмбеддер;
      `tests/integration/test_rag.py` (9) — парсинг, ingest, retrieval,
      `/v1/ingest`, RAG-контекст (gm содержит, club — нет).
- [x] **Документация:** отчёты `reports/iteration-9/`, `CHANGELOG.md`,
      `KNOWN_ISSUES.md` (+KI-025, KI-026), `README.md`, `docs/README.md`,
      `PROMPT_CURRENT.md`, `TODO.md`.

### Живая демонстрация

```
POST /v1/ingest (2 партии с комментариями) → 200 {"ingested": 2, "ids": [57, 58]}
query FEN после 3...a6 → r1bqkbnr/1ppp1ppp/p1n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4
hit 1: «Развитие с темпом / Испанская партия, давление на c6»   ← нужная партия
hit 2: «Ферзевый гамбит»
```

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Настоящие эмбеддинги (`bge-m3`, 1024) | Нет sentence-transformers/torch; у LM Studio модель 768 dims ≠ `vector(1024)` | KI-025 |
| Аутентификация `/v1/ingest` | JWT/роли — Фаза 5 | KI-026 |
| Кэш retrieval по `fen_hash` | `retrieval_cache` есть в схеме, но пока не используется | T-018/T-108 |

### Метрики

- **Backend-тесты:** **181 passed, 1 xfailed** (18 файлов; +16 к итерации 8).
  Полный прогон — 167.14 с.
- **ruff:** 0; **mypy (strict):** 0 ошибок, **41** файл.
- **RAG:** ingest 2 партий — 200, retrieval вернул нужную партию первой.
- **KI:** 26 записей, 22 Open, 4 Closed.
- **Стабильность:** два прогона подряд зелёные (192.79 с и 151.16 с).
- **Время итерации:** ≈ 0.5 ч.

### Что дальше

- **T-018** (частично сделан): retrieval-слой уже подмешивается для `gm`;
  осталось: кэш `retrieval_cache`, настройка top-k по уровням, метрика
  релевантности.
- Затем **Фаза 5**: React-клиент (T-019…T-022), аутентификация (KI-026).

---

## Итерация 12 — Сброс БД, ingest, `PATCH` и чат (T-021b, T-022)

**Дата:** 2026-10-28
**Статус:** ⚠️ завершена в серверном объёме; T-021 не закрыта полностью
из-за расхождения состояния checkout (KI-027)
**Отчёты шагов:** `docs/development/reports/iteration-12/`

### Что сделано

- [x] **Сброс БД (KI-029).** `docker compose down -v` удалил тома
      `pg_data`/`redis_data`, `up -d postgres redis` — оба сервиса
      `healthy`, `alembic upgrade head` → `b802598c3778`, `alembic check` —
      без дрейфа. Ошибка `password authentication failed for user "chess"`
      устранена (старый пароль жил в томе).
- [x] **CLI-ingest.** Создан `scripts/ingest_pgn.py` (`--file`, `--limit`,
      `--source`), в `ingest_pgn_text` добавлен `limit`. Загружено
      **102 партии** из `data/`: 10 + 42 + 50; 99 — с аннотациями,
      эмбеддинг заполнен у всех.
- [x] **T-021b.** `PATCH /v1/commentaries/{id}`: обновляет текст,
      `human_edited = true`, пишет `edits` (оригинал → правка), возвращает
      `edit_id`; 404/422. Тесты: 4 passed.
- [x] **T-022 (сервер).** `POST /v1/chat`: `analysis_id` + `level` +
      `message` → пакет фактов из БД → LLM по уровню → валидатор SAN с
      retry → `{"reply", "tokens_used"}`. Rate limiting, 404/422/502/503/504.
      Тесты: 9 passed (включая живой диалог с LM Studio).
- [x] **Документация:** `TODO.md` (T-021b, статус T-021/T-022),
      `CHANGELOG.md`, `KNOWN_ISSUES.md` (KI-027…KI-029), отчёты шагов.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| UI-панель комментария, SSE, inline-редактор (T-021a) | В checkout нет T-019/T-020 и всех фронтенд-коммитов итераций 10–11 — `web/src/App.tsx` остаётся скелетом | KI-027 |
| SSE-стриминг и React-чат-клиент (T-022b) | Серверный JSON-контракт выполнен; клиент и потоковая отдача — часть отсутствующего UI | KI-028, KI-027 |
| Семантическая проверка атрибуции оценок в ответах чата | Валидатор проверяет только легальность SAN | KI-023 |

### Живая демонстрация

Партия №10 из `data/sample.pgn` (Kobalija — Aseev, 38 полуходов),
`POST /v1/analyze` с уровнем `club` — 10.9 с, `analysis_id=25`,
2 ошибки и 1 неточность. Вопрос и ответ:

```
Вопрос: Почему ход 12.Bb3 был ошибкой и что было лучше?
Ответ:  Ход 12. Bb3 был ошибкой, потому что оценка после него +0.42
        (+42 cp) за белых, а лучший ход движка — e5, который приводит
        к потере 112 cp (класс: mistake). Рекомендация для club — e5.
Токенов: 1950 (1875 in + 75 out), 11.8 с, LM Studio qwen/qwen3-4b-2507.
Валидатор: все SAN-ходы ответа легальны.
```

PATCH на реальном комментарии (DeepSeek, `analysis_id=25`):
`commentary_id=11` (2017 символов) → PATCH → `human_edited=true`,
`edit_id=11`; в `edits` одна запись `2017 → 2095` символов.

### Метрики

- **Backend-тесты:** **194 passed, 1 xfailed** (20 файлов; +13 к итерации 9).
  Полный прогон — 270.85 с.
- **Новые тесты:** PATCH — 4 passed (4.41 с); чат — 9 passed (17.80 с).
- **ruff:** 0; **mypy (strict):** 0 ошибок, **44** файла.
- **RAG:** 102 партии, 99 с аннотациями, 0 пустых эмбеддингов.
- **Живой чат:** 11.8 с / 1950 токенов; анализ 38 полуходов — 10.9 с.
- **KI:** 29 записей, 24 Open, 5 Closed (KI-029 закрыт).
- **Время итерации:** ≈ 1 ч.

### Что дальше

- Восстановить/выполнить T-019, T-020 и T-021a (UI) — без этого T-021
  остаётся `[~]`.
- SSE-эндпоинт чата и история диалога (KI-028).
- T-018: кэш `retrieval_cache`, top-k по уровням, метрика релевантности.

---

## Итерация 13 — Планы Production Hardening и Admin Panel (T-028…T-035)

**Дата:** 2026-10-28
**Статус:** ✅ завершена (документационная, код не менялся)
**Отчёты шагов:** не создавались — итерация состоит из одного
документационного шага

### Что сделано

- [x] **`TODO.md`:** добавлена **Фаза 7. Production Hardening & Admin
      Panel** из 8 задач (T-028…T-035) с зависимостями, DoD и оценками
      (≈25 человеко-дней); расширен Backlog задачами T-111…T-119
      (multi-tenant, A/B промптов, алерты стоимости, PII-редакция,
      backup/DR, K8s, canary, SSO, legal hold).
- [x] **`docs/development/DESIGN_ADMIN_AND_OPS.md`:** новая архитектура —
      схема БД (9 новых таблиц + изменения `users`/`annotated_games`),
      выбор `starlette-admin` с обоснованием, RBAC-матрица, страницы
      админки, OTel-трейсинг и атрибуты спанов, FinOps
      reserve→reconcile, exact/semantic-кэш, слои защиты от prompt
      injection, append-only аудит с hash-chain, план внедрения и
      критерии приёмки.
- [x] **`SPEC.md` §10:** добавлены Admin Control Plane, Observability
      (LLMOps) и FinOps; ссылка на дизайн-документ; прежний общий пункт
      «Наблюдаемость» заменён конкретным.
- [x] **`docs/README.md`:** дизайн-документ добавлен в индекс
      архитектуры; диапазон TODO обновлён до T-001…T-035.
- [x] **`CHANGELOG.md`:** запись о документационном плане.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Миграции и таблицы `prompts`, `audit_logs`, `llm_calls` и др. | Итерация чисто документационная; реализация — T-028…T-035 | TODO, Фаза 7 |
| Код `/admin`, OTel, бюджетов, кэша, guardrails | То же | T-028…T-035 |
| Обновление `PROMPT_CURRENT.md` | Не входит в DoD; метрики кода не менялись | — |

### Метрики

- **Документы:** 1 новый (`DESIGN_ADMIN_AND_OPS.md`), 4 обновлённых
  (`TODO.md`, `SPEC.md`, `docs/README.md`, `CHANGELOG.md`) +
  `RELEASES.md`.
- **Планирование:** 8 задач Фазы 7, 9 задач Backlog, 9 новых таблиц в
  дизайне.
- **Код/тесты:** без изменений (194 passed, 1 xfailed — результат
  итерации 12).
- **Время итерации:** ≈ 0.5 ч.

### Что дальше

- **T-028 (Admin Control Plane):** миграция `users.role`, монтирование
  `starlette-admin`, RBAC, мягкое удаление RAG.
- Затем T-035 (аудит) — до промптов и бюджетов, чтобы фиксировать
  изменения.
- T-029 → T-030 → T-031 → T-032 → T-033 → T-034 по плану §11
  дизайн-документа.

---

## Итерация 14 — Финал MVP и релиз v0.1.0 (T-019…T-026)

**Дата:** 2026-10-28
**Статус:** ✅ MVP-релиз готов в объёме T-023/T-025/T-026; ⚠️ качество
LLM ниже порога 7/10 (KI-030), T-024 и T-027 остаются открытыми
**Отчёты шагов:** `docs/development/reports/iteration-14/`

### Что сделано

- [x] **SPA (T-019…T-022):** React 19 + Vite: PGN, доска из FEN, список
      ходов, оценки/классы/мотивы, панель комментария с inline-редактором
      и PATCH, чат по партии. 14 Vitest-тестов.
- [x] **API для UI:** `initial_fen` в `/v1/analyze` и `fen` у каждого
      хода; статика `web/dist` раздаётся FastAPI `StaticFiles`.
- [x] **Docker production (T-025):** multi-stage Dockerfile (Node →
      Python + SPA), non-root, Stockfish 17, `docker-compose.prod.yml`,
      loopback-порты БД/Redis, healthcheck. Образ 512 MB; стек поднят на
      `8011/5435/6382`, `/v1/health` = `ok`, `/` отдаёт SPA.
- [x] **LLM-as-judge (T-023):** `scripts/evaluate_llm.py` — 5 партий ×
      `club`/`gm`; 9 из 10 комментариев оценены (один `club` — 503);
      полный пакет фактов передан судье (`scripts/rejudge_llm.py`).
- [x] **Документация (T-026):** USER_GUIDE, ASSISTANT_LEVELS,
      ARCHITECTURE, MANUAL_TESTING_GUIDE; README, CHANGELOG 0.1.0,
      GETTING_STARTED, PROMPT_CURRENT, docs/README.
- [x] **QA:** `ruff` 0, `mypy` 0, `pytest` 195 passed + 1 xfailed,
      `pnpm typecheck/test/build` зелёные, Docker build/up/health.

### LLM-as-judge (T-023)

| Шкала | Средняя | Оценено |
| :--- | ---: | ---: |
| Фактическая точность | **4.78 / 10** | 9 |
| Полезность | **5.22 / 10** | 9 |
| Стиль | **5.56 / 10** | 9 |
| **Итог** | **5.19 / 10** | 9 из 10 |

По уровням: `club` — 3.25 / 4.00 / 4.25; `gm` — 6.00 / 6.20 / 6.60.
Один `club`-комментарий не сгенерировался (`503`, LM Studio не загрузила
модель). Судья отмечает перепутанные номера полуходов, неверную
атрибуцию «лучшего хода» и выдуманные оценки. Порог 7/10 **не
достигнут** → **KI-030** (план: сократить пакет фактов, усилить промпт,
few-shot, 8B/облачная mini, повторный eval). Релиз не блокируется
решением итерации.

### Docker production (T-025)

```
image: chessannotator-prod-app:latest  512 MB
app        healthy  127.0.0.1:8011->8000
postgres   healthy  127.0.0.1:5435->5432
redis      healthy  127.0.0.1:6382->6379

GET /v1/health -> 200 {"status":"ok",
  "components":{"db":"ok","redis":"ok","stockfish":"ok","llm":"ok"}}
GET /        -> 200 text/html (SPA)
POST /v1/analyze -> 200 (analysis_id=1)
POST /v1/chat    -> 200 (948 токенов)
```

Публикация в GHCR не выполнялась (нет registry-доступа) — T-027.

### DoD итерации

| Пункт | Статус |
| :--- | :--- |
| Оценка LLM, отчёт `LLM_EVAL.md` | ✅ 5.19/10, 9/10, KI-030 |
| Docker production проверен и безопасен | ✅ образ, стек, health, loopback-порты |
| README, USER_GUIDE, CHANGELOG под v0.1.0 | ✅ |
| `pytest`, `pnpm test` зелёные | ✅ 195+1 xfail; 14 passed |
| T-023, T-025, T-026 `[x]` | ✅ |

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Качество LLM ≥ 7/10 | Локальная 4B-модель, длинный пакет фактов, таймауты; нужен тюнинг промптов/модели | KI-030 |
| Слепой тест уровней | Нужны 10 живых шахматистов | T-024 |
| Тег v0.1.0 и GHCR | Нет registry-доступа; T-024 не закрыт | T-027 |
| SSE-стриминг | Синхронный JSON-контракт | KI-028 |
| Playwright/скриншот-тест | Заменены Vitest + интеграционным тестом SPA | T-020 |

### Метрики

- **Backend-тесты:** 195 passed, 1 xfailed (21 файл), 199.09 с.
- **Frontend-тесты:** 14 passed (2 файла), typecheck/build — 0.
- **ruff:** 0; **mypy:** 0, 44 файла.
- **Docker:** образ 512 MB, 3 контейнера healthy, health `ok`.
- **LLM-eval:** 5.19/10; 9/10; club 3.83 средняя, gm 6.27 средняя.
- **KI:** 30 записей, 23 Open, 7 Closed.
- **Время итерации:** ≈ 1.5 ч.

### Что дальше

1. **KI-030:** сократить пакет фактов, усилить промпт и few-shot,
   повторить eval; при необходимости — 8B/облачная mini для `club`.
2. **T-024:** слепой тест 10 шахматистов.
3. **T-027:** тег `v0.1.0`, GHCR, релиз-ноты после registry-доступа.
4. **Phase 7:** admin control plane, prompt registry, OTel, FinOps,
   semantic cache, guardrails, audit.

---

## Итерация 15 — Синхронизация UI и SSE-стриминг

**Дата:** 2026-10-28
**Статус:** ✅ завершена; KI-024…KI-028 закрыты
**Отчёт шага:** `docs/development/reports/iteration-15/STEP-01_SYNC_SSE.md`

### Что сделано

- [x] **Синхронизация Итераций 10–12:** `react-chessboard` 5.12.1
      (`options={{position}}`), `chess.js` `history({verbose:true})` для
      FEN из PGN, `react-i18next` RU/EN, Zustand `analysisStore.ts` с
      persist в `sessionStorage`.
- [x] **X-Admin-Token:** `POST /v1/ingest` защищён `verify_admin_key`
      (constant-time compare с `ADMIN_API_KEY`), 401/503.
- [x] **RAG 768:** `LMStudioEmbedder` (`/v1/embeddings`,
      `text-embedding-nomic-embed-text-v1.5`, L2), миграция
      `d2e3f4a5b6c7`, 102 партии переиндексированы; hash — fallback
      тестов.
- [x] **`GET /v1/games/{id}`:** сохранённый анализ + `analysis_level` +
      `commentaries`; `games.analysis_level`/`analysis_time_ms`.
- [x] **SSE:** `stream_generate` в OpenAI-клиенте,
      `POST /v1/commentary/stream` и `/v1/chat/stream` (sse-starlette),
      события `meta`/`delta`/`done`/`error`, финальный SAN-валидатор;
      фронтенд читает `ReadableStream` и печатает постепенно.
- [x] **Тесты:** +1 games, +3 streaming, +3 SSE-parser, +2 LMStudio
      embedder, +1 admin auth.

### Проверки

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend | `pytest -q` | **205 passed, 1 xfailed** (196.60 с) |
| Frontend | `pnpm --dir web test` | **20 passed** (4 файла) |
| Types | `pnpm --dir web typecheck` | exit 0 |
| Build | `pnpm --dir web build` | JS 406.59 kB / CSS 8.76 kB |
| Lint | `ruff check .` | 0 |
| Mypy | `mypy app` | 0, 49 файлов |
| Migrations | `alembic check` | без дрейфа, `d2e3f4a5b6c7` |
| Embeddings | `vector_dims` / NULL | 768 / 0 |
| Live stream | LM Studio | чанки `Готов`/`о` за 1.05 с |
| Live chat SSE | `/v1/chat/stream`, `analysis_id=25` | 200, 16.7 с, 23 delta, `done`, 961 токен |
| Prod image | `docker build` | 513 MB, app `healthy`; миграции `c1f2→d2e3` |
| Prod SPA/GET/SSE | `127.0.0.1:8011` | `/` 200, `/v1/games/1` 200, `/v1/chat/stream` 200 (520 токенов) |
| SSE | `test_streaming.py` | 3 passed |

### Закрытые KI

| KI | Как закрыт |
| :--- | :--- |
| KI-024 | SSE-стриминг: текст виден сразу; скорость модели не изменилась |
| KI-025 | `LMStudioEmbedder` 768 + миграция + re-embed 102 |
| KI-026 | `X-Admin-Token`, constant-time compare |
| KI-027 | UI/бэкенд-фичи восстановлены |
| KI-028 | `/v1/chat/stream` + typewriter-клиент |

KI-030 (качество LLM 5.19/10) остаётся открытым и не блокирует итерацию.

### Ограничения

- История чата между запросами не сохраняется.
- Playwright/скриншот-тест не внедрён; T-020 остаётся `[~]`.
- Токены в SSE — оценка `chars/4`, не биллинг.
- Production-образ пересобран (513 MB); миграции `c1f2→d2e3` применены,
  `/v1/health` = `ok`.

### Что дальше

Phase 7 / T-028 (Admin Control Plane).

---

## Итерация 16 — Admin Panel, KI-030, OTel (T-028, T-030, частично)

**Дата:** 2026-10-29
**Статус:** ⚠️ частично: T-028/T-030 в объёме итерации; KI-030 Open
**Полный отчёт (7 разделов):**
`docs/development/reports/v0.1.0/ITERATION-16.md`

### Что сделано

- `/admin` на `starlette-admin`: `X-Admin-Token` / cookie-сессия,
  401/403, read-only views `annotated_games`, `commentaries`, `edits`.
- OTel-спаны `llm_generate`/`llm_stream` с моделью, уровнем, токенами,
  стоимостью и статусом; `console`/`otlp` при `OTEL_ENABLED=true`.
- `club`: промпт сокращён, few-shot без SAN, пакет — топ-3 без PV.
  Валидатор не считает одиночную клетку ходом.
- `RULES.md` §2.18 — стандарт отчётов из 7 разделов.

### Метрики

- Backend-тесты: **218 passed, 1 xfailed** (182.06 с).
- `ruff` — 0; `mypy app` — 0 (51 файл).
- LLM `club`: **4.40/10** (было 3.83), 5/5 без 5xx; prompt-токены
  ≈ 3.4k → 1.9k.
- Админка: 401/403/200, login 303 + cookie, 3 списка 200.
- OTel: спан `llm_generate`, status OK, 2.47 с.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| RBAC, `users.role`, Redis-сессии, soft-delete RAG | Следующий шаг control plane | T-028 |
| `trace_id` propagation, `llm_calls`, спаны HTTP/validator/RAG | Базовый OTel-слой только включён | T-030 |
| Качество LLM ≥ 7/10 | 4B-модель путает атрибуцию; KI-023 | KI-030 |
| Prompt Editor, Export, Audit Log | Зависят от T-028/T-035 | T-029, T-034, T-035 |

### Что дальше

T-028-полный (роли + Redis-сессии + soft-delete), T-029 (Prompt Registry),
T-030-полный (`llm_calls`, propagation), T-035 (audit).

---

## Итерация 17 — Prompt Management и FinOps (T-029, T-031)

**Дата:** 2026-10-29
**Статус:** ✅ T-029/T-031 в объёме итерации; остатки — T-029b/T-031b
**Полный отчёт (7 разделов):**
`docs/development/reports/v0.1.0/ITERATION-17.md`

### Что сделано

- Таблица `prompts` (`level`, `content`, `version`, `updated_at`),
  миграция `e3f4a5b6c7d8`; seed из `.txt` при старте.
- `load_system_prompt` читает БД с in-memory кэшем, `reset_prompt_cache`;
  редактируемый `PromptAdmin`, версия +1, hot-reload без рестарта.
- `TokenUsageTracker` и `TokenBudgetMiddleware`: дневные лимиты
  `free`/`pro`, `429 token_budget_exceeded` с `X-RateLimit-Reset`.
- `cost_usd` по ценам из конфига в `LLMUsage`, `commentaries` и OTel;
  KI-021 закрыт.

### Метрики

- Backend-тесты: **235 passed, 1 xfailed** (182.79 с).
- `ruff` — 0; `mypy app` — 0 (52 файла).
- `alembic check` — без дрейфа; head `e3f4a5b6c7d8`.
- Prompt smoke: POST 303, version +1, следующий `load_system_prompt`
  вернул новый текст без ручного reset.
- Token smoke: 200 / 200 / 429, счётчик 120 токенов, TTL до UTC-полуночи.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| История версий/rollback/API/аудит промптов | Одна строка на уровень; нужен registry | T-029b |
| Multi-worker инвалидация кэша | Нужен Redis Pub/Sub | KI-032 |
| Доверенный тир/субъект бюджета | Нет JWT/ролей | KI-031, T-028 |
| Durable `usage_daily`, месячные лимиты, дашборд | Горячий путь MVP | T-031b |

### Что дальше

T-029b (registry), T-031b (durable FinOps), T-028-полный (JWT/роли),
T-035 (аудит).

---

## Итерация 18 — Guardrails и Security Audit (T-033, T-035)

**Дата:** 2026-10-29
**Статус:** ✅ T-033/T-035 в объёме итерации; KI-031/KI-032 закрыты
**Полный отчёт (7 разделов):**
`docs/development/reports/v0.1.0/ITERATION-18.md`

### Что сделано

- `app/security/guardrails.py`: NFKC, control/zero-width/bidi, лимит
  2000, 7 RU/EN-правил; `/v1/chat` и `/chat/stream` → 422
  `prompt_injection` + аудит; sysprompt запрещает раскрывать инструкции.
- `audit_logs`: hash-chain, advisory-lock, trigger `UPDATE/DELETE/
  TRUNCATE → append-only`; аудит промптов, PATCH комментариев и инъекций;
  read-only view + страница «Проверить целостность».
- KI-032: Redis Pub/Sub `prompts_updates`, watcher в lifespan,
  публикация после commit.
- KI-031: тир только по IP из `PRO_IPS`; `X-User-Tier`/`X-User-Id`
  игнорируются; XFF — только при `TRUST_FORWARDED_FOR=true`.

### Метрики

- Backend-тесты: **257 passed, 1 xfailed** (177.23 с).
- `ruff` — 0; `mypy app` — 0 (56 файлов).
- `alembic check` — без дрейфа; head `f4a5b6c7d8e9`.
- Injection smoke: 422, audit `prompt_injection.blocked`, verify OK,
  `UPDATE` заблокирован триггером.
- Prompt audit smoke: 303, `prompt.update`, Pub/Sub subscribed,
  chain OK (5 записей).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Классификатор, `<user_question>`, style, output filter, корпус атак | Regex — первый барьер | T-033b |
| IP/UA/request_id, аудит login/бюджетов/экспорта, SIEM, auditor | Остаток T-035 | T-035b |
| Атомарный аудит правки промпта | starlette-admin коммитит до hook | KI-033 |
| TTL-fallback Pub/Sub | Сообщение может потеряться | T-029b |
| JWT-тир и роли | Нет JWT | T-028/T-031b |

### Что дальше

T-033b (классификатор/корпус), T-035b (полный аудит/SIEM),
T-028-полный (JWT/роли), T-029b (registry/TTL).
