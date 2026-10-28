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
