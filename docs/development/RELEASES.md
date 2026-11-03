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
| Перенос плоских тестов итерации 1 в `tests/unit\|integration` | Не блокирует T-007, требует аккуратного разделения | KI-010 (T-008) |
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

---

## Итерация 19 — Semantic Cache и LoRA (T-032, T-104)

**Дата:** 2026-10-29
**Статус:** ✅ T-032/T-104 в объёме итерации; KI-033 закрыт
**Полный отчёт (7 разделов):**
`docs/development/reports/v0.1.0/ITERATION-19.md`

### Что сделано

- `semantic_cache` (vector 768, HNSW): exact `fen_hash+level+prompt_hash`,
  semantic cosine < 0.05, TTL 30 дней; `/v1/commentary`, `/v1/chat` и
  SSE отдают кэш; `prompt_hash` включает sysprompt/модель/style/question.
- `scripts/export_dataset.py` — JSONL из `edits`; `scripts/train_lora.py`
  — QLoRA-каркас с `--dry-run`; `LORA_ADAPTER_PATH` → basename adapter
  для vLLM.
- KI-033: аудит правки промпта перенесён в `after_edit` и пишется в той
  же транзакции; сбой аудита откатывает правку.

### Метрики

- Backend-тесты: **275 passed, 1 xfailed** (187.47 с).
- `ruff` — 0; `mypy app` — 0.
- `alembic check` — без дрейфа; head `a5b6c7d8e9f0`.
- Cache smoke: первый комментарий 43.54 с, второй **0.030 с**
  (1440×, `<100 мс`), `model=semantic-cache`, tokens_used=0.
- LoRA smoke: export 1 записи, `train_lora --dry-run` — 0 ошибок.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Redis hot-слой, метрики hit/miss, engine-cache, `cache=off` | Базовый durable-кэш | T-032b |
| Реальное LoRA-обучение | Нет GPU; каркас + dry-run | T-104 |
| Админ-страница Export, checksum, аудит выгрузки | Вне DoD итерации | T-034 |
| Семантический кэш может переиспользовать комментарий другой партии | Ограничение FEN-подхода | «Что не так» |

### Что дальше

T-032b (hot-слой/метрики), T-034 (admin export), T-033b (guardrails),
T-035b (аудит/SIEM).

---

## Итерации 20–23 — Frontend UX, LLM Evals, CI (T-036…T-040)

**Дата:** 2026-10-29
**Статус:** ✅ T-036…T-040 в объёме ночной смены
**Полный отчёт (7 разделов):**
`docs/development/reports/v0.1.0/ITERATION-20-23.md`

### Что сделано

- **T-036/T-037:** `GET /v1/games` (20 последних), `GET
  /v1/users/me/usage`, `/history`, `/analysis/:id`, usage-badge
  `Free: 12k/50k` + модалка Pro, SPA-fallback.
- **T-037:** история чата в Zustand/sessionStorage, `history` в
  `/v1/chat` и `/v1/chat/stream`, повторный guardrail user-реплик.
- **T-038:** mobile 1280/820/640/480, shimmer-skeleton, Error Boundary.
- **T-039:** `llm_evaluations` + read-only admin view + запись из
  `evaluate_llm.py`.
- **T-040:** pip/pnpm cache, `workflow_dispatch`, полный CI-набор.

### Метрики

- Backend-тесты: **286 passed, 1 xfailed** (193.14 с).
- Frontend: **20 passed**, `typecheck`, `build` — зелёные.
- `ruff` — 0; `mypy app` — 0; `alembic check` — без дрейфа;
  head `b6c7d8e9f0a1`.
- Docker: образ собирается.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Auth/скоуп истории | Нет JWT/аккаунтов; KI-035 | T-028 |
| Графики LLM-evals | Таблица достаточна для MVP | T-039b |
| E2E браузерных сценариев | Нет Playwright в окружении | T-036b |
| Реальный прогон GitHub Actions | Нет push/CI-раннера локально | T-040 |

### Что дальше

T-028 (auth/скоуп), T-039b (графики/фильтры evals), T-036b (E2E),
T-032b (cache hot-layer).

---

## Итерация 24 — JWT-авторизация и изоляция данных (T-041, KI-035)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. **KI-035 закрыт**; заведены KI-036, KI-037, KI-038.
**Отчёт:** `docs/development/reports/v0.1.0/ITERATION-24.md` (7 разделов, RULES §2.18)

### Что сделано

- [x] **Схема и auth-фундамент.** Миграция `c7d8e9f0a1b2`: `users.role`
  (enum `admin_role`: user/admin/auditor), `is_active`, `password_hash`,
  `last_login_at`; `commentaries.user_id`, `edits.user_id`; индексы
  `ix_games_user_id_created`, `ix_users_role`, `ix_commentaries_user_id`,
  `ix_edits_user_id`. Пакет `app/auth/` (passlib/bcrypt, python-jose,
  access 15 мин / refresh 7 дней, `get_current_user`, `require_admin`,
  проверки доступа к партии/комментарию).
- [x] **`/auth` API:** register (201), login (200 access+refresh+user),
  refresh (ротация), me; 401/403/409/422 по контракту.
- [x] **Изоляция данных (KI-035):** analyze пишет `games.user_id`, list/get
  фильтруются по владельцу (admin — все, legacy NULL-owner — только
  admin, чужой id → 404), commentary/chat проверяют доступ до LLM,
  `commentaries.user_id`/`edits.user_id` заполняются (включая SSE).
- [x] **Usage по ролям:** admin/auditor → pro, user → free, subject
  `user:{id}`; анонимно — прежний IP-путь.
- [x] **Фронтенд:** `/login`, токены в `localStorage`, Bearer + refresh
  interceptor, `AuthGate`, профиль/выход в шапке.
- [x] **Salvage итераций 10–11:** `EmbeddingError` + батчевый `embed_many`,
  safe `--limit=10` в CLI, расширенные тесты GET parity/FEN/404,
  детерминированный порядок мотивов.
- [x] **Документация:** `KNOWN_ISSUES` (KI-035 → Closed, +036…038),
  `CHANGELOG`, `README`, `TODO` (T-041, T-028), `docs/README`,
  `PROMPT_CURRENT`, `.env.example`, `ITERATION-24.md`.

### Метрики

- **Backend-тесты:** **382 passed, 1 skipped, 1 xfailed** (48 файлов;
  +96 к итерации 23), 210.78 с. Skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012.
- **Frontend-тесты:** **36 passed** (6 файлов; +16 к итерации 23).
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 67 файлов.
- **Миграции:** head `c7d8e9f0a1b2`; upgrade/roundtrip/`alembic check` — чистые.
- **Сборка фронтенда:** 99 модулей, 4.38 с, JS 465 КБ (gzip 146 КБ).
- **Smoke (uvicorn + curl):** register 201; login 200
  (`access_token`, `refresh_token`); `GET /v1/games` с токеном 200 `[]`,
  без токена 401 `unauthorized`; `POST /v1/analyze` без токена 401;
  неверный пароль 401.
- **KI:** 38 записей, 21 Open, 17 Closed.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Отзыв refresh-токенов, logout на сервере, 2FA, верификация email/сброс пароля | Отдельный объём; итерация закрывала KI-035 минимальным JWT-слоем | KI-037 |
| httpOnly-cookie вместо `localStorage` | Требует CSRF-стратегии/BFF; фронтенд по постановке использует `localStorage` | KI-037 |
| Per-user дневные бюджеты токенов | `TokenBudgetMiddleware` остался IP-based (T-031) | KI-038 |
| Обновление `passlib` на Argon2id | passlib 1.7.4 + bcrypt 5 несовместимы; пин bcrypt<4.1 | KI-036 |
| JWT для `/admin` | `/admin` по-прежнему использует парольную сессию `ADMIN_PASSWORD` | T-028 |

### Что дальше

- **KI-037:** refresh-token store/denylist, `POST /auth/logout`, httpOnly
  cookie/BFF, лимиты попыток по email, email-flow.
- **KI-038/T-031b:** брать subject/tier из JWT в middleware, durable
  агрегаты расходов по пользователю/организации.
- **T-028:** RBAC внутри `/admin` (admin vs auditor), серверные сессии в
  Redis, мягкое удаление RAG.
- **T-036b:** Playwright e2e (login → анализ → комментарий).

---

## Итерация 25 — LoRA Fine-tuning и Playwright E2E (T-104, T-036b)

**Дата:** 2026-10-29
**Статус:** ✅ завершена в объёме DoD; **KI-030 остаётся Open** (GPU нет,
обучение не выполнялось); заведены KI-039 и KI-040.
**Отчёт:** `docs/development/reports/v0.3.0/ITERATION-25.md` (7 разделов,
RULES §2.18)

### Что сделано

- [x] **T-104, реальный LoRA-пайплайн.** `scripts/train_lora.py` —
  настоящий SFT на `peft`/`transformers`: QLoRA 4-bit
  (`--load-in-4bit`/`--no-4bit`), r=16, `lora_alpha=32`,
  `target_modules=["q_proj","v_proj"]`, 3 эпохи, lr=2e-4,
  gradient checkpointing, маскирование `labels=-100` на
  system/user/служебных токенах (обучается только assistant),
  right-padding-коллатор. `--dry-run` (без torch), `--check-env`,
  понятные `SystemExit` для «нет зависимостей», «нет CUDA», «нет/пуст/
  невалиден датасета», CUDA OOM. `--output-dir` по умолчанию —
  `models/lora-club-v1`.
- [x] **Датасет и валидация.** `scripts/export_dataset.py` пишет строгую
  схему `system → user → assistant` (system — `app/prompts/<level>.txt`);
  валидатор проверяет порядок ролей, непустой content, `schema_version=1`,
  лимиты. Синтетическая фикстура `tests/fixtures/lora_sample.jsonl` для
  офлайн-прогона.
- [x] **Конфиг/фабрика.** `Settings.lora_model` отдаёт basename адаптера
  только если путь существует (файл или каталог с `adapter_config.json`),
  иначе базовую `VLLM_MODEL`; `lora_adapter_available`/`lora_adapter_dir`;
  `app/llm/factory.py` логирует `llm_lora_adapter_enabled`/`unavailable`;
  `gm` не изменён.
- [x] **Документация.** `docs/development/reports/LORA_TRAINING.md`:
  prerequisites, экспорт, схема, гиперпараметры, dry-run с фактическим
  выводом, RunPod/Colab, стоимость, сохранение адаптера, vLLM
  `--lora-modules`, eval `evaluate_llm.py`, ограничения (KI-030/KI-039).
  Обновлены `.env.example`, `.gitignore` (`models/`).
- [x] **T-036b, Playwright E2E.** `@playwright/test` 1.64.1;
  `web/playwright.config.ts` (projects `chromium` + `mobile`, baseURL
  `http://127.0.0.1:5173`, webServer Vite, trace on-first-retry,
  screenshot only-on-failure, video retain-on-failure). 5 сценариев в
  `web/e2e/`: `auth`, `analyze`, `commentary` (SSE + PATCH), `history`,
  `limits`; page.route-моки API по контрактам `web/src/api.ts`; реальный
  backend-режим `E2E_REAL_BACKEND=1`. Скрипты `test:e2e`/`test:e2e:ui`;
  vitest исключает `e2e/**`; CI-job `e2e` + `upload-artifact` при
  падении (trace/screenshot).
- [x] **T-042, загрузка новых PGN в RAG.** В `annotated_games` загружены
  `data/sample3.pgn` (500), `data/sample4.pgn` (13), `data/sample5.pgn`
  (9) — **522 партии**, `source=new_samples`, `--limit 1000` (дефолт
  `--limit 10` не покрывал файлы). Baseline `my-collection` (102 записи:
  10+42+50) воспроизведён историческим рецептом итерации 12; итог
  **624** записи, 621 с аннотациями, 0 `NULL` embedding. В
  `scripts/evaluate_llm.py` добавлен фильтр `--source` (фиксирует
  eval-набор для сравнения LoRA), описан в `LLM_EVAL.md`.
- [x] **Документация итерации:** `KNOWN_ISSUES` (KI-030 — прогресс без
  обучения, +KI-039/KI-040), `CHANGELOG`, `README`, `TODO` (T-104,
  T-036b, T-042), `RELEASES`, `tests/fixtures/README.md`,
  `LLM_EVAL.md`, отчёт `ITERATION-25.md`.

### Метрики

- **Backend-тесты:** **435 passed, 1 skipped, 1 xfailed** (48 файлов;
  +53 к итерации 24), 256.59 с, exit 0. Skip — нет `DEEPSEEK_API_KEY`,
  xfail — KI-012. LoRA-набор: 60 тестов (было 7).
- **Frontend-тесты:** **36 passed** (6 файлов).
- **E2E:** mocked — **10 passed** (5 сценариев × chromium/mobile, 10.5 с);
  real backend — **5 passed** (chromium, 1.4 мин; commentary 1.3 мин).
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 67 файлов.
- **Сборка фронтенда:** 99 модулей, 6.33 с; JS 465.15 КБ (gzip 146.41),
  CSS 14.29 КБ (gzip 3.77), dist 480 186 B.
- **T-104 CLI:** `--dry-run` exit 0 (4 записи, club=4, ~880 токенов,
  r=16/alpha=32/q_proj,v_proj); реальный запуск без torch → exit 1 с
  инструкцией; `--check-env` exit 0.
- **RAG (T-042):** `annotated_games` — **624** записи (102
  `my-collection` + **522** `new_samples`: 500+13+9), 621 с
  аннотациями, 0 `NULL` embedding; ingest exit 0 для всех трёх файлов.
- **KI:** 40 записей, 23 Open, 17 Closed.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Реальное обучение LoRA и замер прироста качества | На машине нет NVIDIA GPU/CUDA, `torch`/`peft` не установлены; DoD T-104 допускает каркас + инструкцию + dry-run | KI-030, LORA_TRAINING.md |
| SFT-пара «факты → комментарий» | `edits` хранит только original→edited; нужен `schema_version=2`/сохранение пакета фактов | KI-039 |
| Реальный backend в CI для E2E | Нужны Postgres/Redis/Stockfish/LLM; real-режим проверен локально | KI-040 |
| Typecheck для `web/e2e/**` | `tsconfig` включает только `src`; Playwright транспилирует без tsc | OBS в отчёте |

### Что дальше

- **KI-030/T-104b:** решить дизайн SFT-пары (KI-039), обучить LoRA на
  RunPod/Colab, прогнать `evaluate_llm.py` на 10–20 партиях (baseline
  `club` 4.40/10), сравнить `factual_accuracy`/`usefulness`.
- **KI-040:** real-backend E2E в отдельном CI-job/nightly (docker compose +
  stub-LLM) либо на self-hosted runner с GPU.
- **T-039b:** графики/фильтры LLM-evals в админке.
- **KI-037/KI-038:** отзыв refresh, httpOnly cookie, per-user бюджеты.

---

## Итерация 26 — Auth Tech Debt и Dataset Schema v2 (KI-036, KI-037, KI-038, KI-039, T-028 финал)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. KI-036, KI-037, KI-038, KI-039 закрыты;
T-028 (JWT-интеграция в `/admin`) закрыт. Заведены KI-041…045.
**Отчёт:** `docs/development/reports/v0.4.0/ITERATION-26.md` (7 разделов,
RULES §2.18)

### Что сделано

- [x] **KI-036, Argon2id.** Убраны `passlib`/`types-passlib`; пароли —
  `argon2-cffi` (`$argon2id$`), лимит 8..256 символов. `verify_and_update`:
  нативный rehash при смене параметров; legacy `$2a$/$2b$/$2y$` —
  `bcrypt.checkpw` → новый Argon2id-хеш, `/auth/login` перезаписывает
  `users.password_hash` в той же транзакции (+`password_rehashed`).
  `bcrypt>=4.1` оставлен только для legacy-verify (KI-042).
- [x] **KI-037, HttpOnly refresh + denylist.** `/auth/login` и
  `/auth/refresh` не возвращают refresh в JSON: HttpOnly-cookie
  (`SameSite=Lax`, `Path=/auth`, `Max-Age`=TTL, `Secure` при
  `AUTH_COOKIE_SECURE=true`). Новый `POST /auth/logout` кладёт `jti` в
  Redis `refresh_denylist:<jti>` (TTL до `exp`) и очищает cookie;
  ротация refresh также отзывает прежний jti. Refresh читается из cookie
  (fallback — JSON-тело для curl/CLI), 401 для отозванного/битого.
  Фронтенд: access и user — только в памяти Zustand, `localStorage` для
  токенов удалён, все fetch — `credentials:"include"`, single-flight
  refresh по cookie, `bootstrapAuth()` при старте, logout через API.
  CORS `allow_credentials=True` подтверждён credentialed preflight-тестом.
- [x] **KI-038, FinOps по пользователю.** `TokenBudgetMiddleware` и
  `/v1/users/me/usage` используют общий резолвер: валидный access JWT →
  `user:{id}` + tier из claim `role` (admin/auditor → pro, user → free);
  аноним/битый токен → IP + `PRO_IPS`. Ключ Redis —
  `usage:<date>:user:<id>`; middleware получает `Settings` в main.py.
- [x] **KI-039, Dataset Schema v2.** `export_dataset.py` по умолчанию
  `schema_version=2`: `system` — промпт уровня, `user` —
  `build_fact_package(...)` (восстановлен из `games/moves/motifs`),
  `assistant` — `edited_text`, `metadata` + `fact_sheet_chars`; пропуски
  (no_game/invalid_pgn/no_analysis/invalid_level) со статистикой;
  `--schema-version 1` — legacy. `train_lora.py` поддерживает `{1,2}`,
  распределение по версиям и предупреждение о v1-rewrite;
  `LORA_TRAINING.md` переписан под v2.
- [x] **T-028 финал: JWT в `/admin`.** `AdminAuthProvider`/`AdminAuthGuard`
  принимают access JWT (`Authorization: Bearer` или cookie
  `access_token`): admin/auditor → 200, user → 403, битый/просроченный →
  403, без кредов → 401; legacy `X-Admin-Token` и парольная форма
  сохранены (RBAC внутри views — KI-045).
- [x] **Документация:** README (auth, статус, тесты), `SPEC.md`,
  `KNOWN_ISSUES` (4 KI → Closed, +KI-041…045), `TODO` (T-028 финал,
  T-043/T-044, T-104 v2), `CHANGELOG`, `RELEASES`, `ITERATION-26.md`,
  `.env.example`.

### Метрики

- **Backend-тесты:** **522 passed, 1 skipped, 1 xfailed** (48 файлов;
  +86 к итерации 25), **218.74 с**, exit 0. Skip — нет
  `DEEPSEEK_API_KEY`, xfail — KI-012. Новых тестовых функций **+81**.
- **Frontend-тесты:** **42 passed** (6 файлов; +6).
- **E2E:** mocked — **10 passed** (5 сценариев × chromium/mobile, 13.5 с);
  real backend (chromium) — **5 passed** (1.3 мин), проверено независимо.
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 69 файлов.
- **Сборка фронтенда:** 6.04 с; JS 464.31 КБ (gzip 146.15), CSS 14.29 КБ
  (gzip 3.77), dist 479 350 B.
- **Argon2id:** хеш `$argon2id$…`; живой bcrypt→argon2 login; cookie
  `refresh_token=…; HttpOnly; Max-Age=604800; Path=/auth; SameSite=lax`;
  logout → denylist → reuse 401; ротация отзывает прежний jti.
- **Admin JWT:** Bearer admin/auditor → 200, user → 403, битый → 403,
  без кредов → 401 (живой uvicorn-smoke).
- **KI:** 45 записей, 24 Open, 21 Closed.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| 2FA, верификация email, сброс пароля, «выйти на всех устройствах», лимит попыток по email | Объём KI-037 закрыт в части cookie/denylist; email-flow/2FA — отдельный слой | KI-041 |
| Удаление `bcrypt` из зависимостей | Нужен для проверки legacy-хешей, пока не замигрированы все пользователи (rehash только при логине) | KI-042 |
| Защита от user-enumeration по таймингу логина | Не входило в DoD; план — dummy-Argon2 для несуществующего email | KI-043 |
| Кэш `load_analyses` при экспорте v2 | N+1 приемлем для сотен правок; оптимизация отложена | KI-044 |
| RBAC внутри `/admin` views (auditor ≠ admin) | Итерация закрывала JWT-вход; политика по views — отдельный шаг (T-028/T-035) | KI-045 |
| Реальное LoRA-обучение на GPU | Нет GPU/CUDA (KI-030 Open); schema v2 подготовлена под первый прогон | KI-030 |

### Что дальше

- **KI-041:** 2FA/email-flow, отзыв всех сессий, пароли в админке вместо
  `ADMIN_PASSWORD`.
- **KI-042/KI-043:** завершить миграцию bcrypt-хешей и убрать зависимость;
  выровнять тайминг логина.
- **KI-044/KI-045:** кэш анализа при экспорте v2; RBAC в admin views.
- **KI-030/T-104b:** обучить LoRA на `schema_version=2` (RunPod/Colab) и
  сравнить с baseline `club` 4.40/10 через `evaluate_llm.py
  --source new_samples`.

---

## Итерация 27 — LLMOps Observability и Security Fixes (T-030/T-030b, KI-043, KI-045)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. KI-043 и KI-045 закрыты; T-030/T-030b закрыты
в объёме MVP; заведены KI-046…048.
**Отчёт:** `docs/development/reports/v0.5.0/ITERATION-27.md` (7 разделов,
RULES §2.18)

### Что сделано

- [x] **T-030, durable `llm_calls`.** Миграция `d8e9f0a1b2c3` (head) —
  таблица по DESIGN §4.4: trace_id/span_id, request_id, actor/game/
  commentary, operation/provider/model/level, prompt_slug/version,
  tokens/cost/latency/cache_hit/status/error_code, created_at;
  `UNIQUE(trace_id, span_id)`, BRIN+DESC-индексы, FK ON DELETE SET NULL.
  Модель `LlmCall`, read-only `LlmCallAdmin` (фильтры по model/operation/
  status).
- [x] **T-030, trace_id.** `app/tracing.py` + `TraceMiddleware` (самый
  внешний): W3C `traceparent` (парсинг/генерация, нулевые id → новые),
  `request.state.trace_id/span_id`, contextvars, structlog
  (`trace_id`/`span_id` в логах), ответ `X-Trace-Id`, OTel-span
  `http_request` с тем же trace id, вложенные `llm_*`-спаны.
- [x] **T-030, запись вызовов.** `app/llm/calls.py`: contextvars +
  `llm_call_context`/`stream_in_call_context`; `record_llm_call`
  fire-and-forget в отдельной сессии, best-effort (ошибка записи не
  ломает ответ); `openai_client` пишет в `finally` для complete и stream
  (ok|error|timeout|invalid, отмена/закрытие стрима); prompt_slug=level,
  prompt_version из `prompts.version`; без текстов/PII. Endpoints
  `/v1/commentary` и `/v1/chat` (sync+SSE) выставляют
  operation/actor_id/game_id.
- [x] **T-030b, OTel-спаны validator/RAG.** `validator.validate`
  (moves/valid/illegal_count) и `rag.retrieve` (top_k/results) с
  `trace.id`; PII не пишется.
- [x] **KI-043, timing-safe login.** `DUMMY_PASSWORD_HASH` (Argon2id);
  для несуществующего email выполняется verify перед тем же 401.
  Живые медианы: 55.4 мс (existing) / 54.8 мс (unknown), ratio 1.01.
- [x] **KI-045, RBAC admin.** `request.state.admin_role`; auditor —
  любые небезопасные методы в `/admin/**` → 403 `admin_forbidden`,
  role-aware hooks (PromptAdmin edit только admin/legacy, import закрыт,
  export auditor'у запрещён); admin/legacy сохраняют права; fail-closed
  fallback auditor.
- [x] **Документация:** README (обновлены статус/таблицы/Observability/
  KI), `KNOWN_ISSUES` (KI-043/045 → Closed, +KI-046…048),
  `TODO` (T-030/T-030b → `[x]`), `CHANGELOG`, `RELEASES`,
  `ITERATION-27.md`.

### Метрики

- **Backend-тесты:** **592 passed, 1 skipped, 1 xfailed** (53 файла;
  +70 к итерации 26), **279.32 с**, exit 0. Skip — нет
  `DEEPSEEK_API_KEY`, xfail — KI-012.
- **Frontend-тесты:** **42 passed** (6 файлов).
- **E2E mocked:** **10 passed** (5 сценариев × chromium/mobile).
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 71 файл.
- **Alembic:** head `d8e9f0a1b2c3`; upgrade/downgrade-цикл и
  `alembic check` — чистые.
- **Сборка фронтенда:** 4.23 с; JS 464.31 КБ (gzip 146.15), CSS 14.29 КБ
  (gzip 3.77).
- **LLMOps smoke:** POST `/v1/chat` с `traceparent` →
  `X-Trace-Id: 4bf92f35-77b3-4da6-a3ce-929d0e0e4736`; в `llm_calls` —
  `trace_id` совпадает, operation=chat, provider=lmstudio, level=club,
  prompt_slug=club, prompt_version=1, tokens 819/29, latency 5176 мс,
  status ok, actor_id/game_id заполнены.
- **Admin smoke:** auditor — `/admin` 200, `/admin/prompt/edit` 403,
  `POST /admin/prompt/delete` 403, `/admin/llm-call/list` 200; admin —
  edit 200, delete 403 (read-only), llm-call list 200; user — 403;
  без кредов — 401.
- **KI:** 48 записей, 25 Open, 23 Closed.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Warmup в `llm_calls`, `commentary_id`, `cache_hit` для semantic-кэша | Запись из LLM-клиента не знает id комментария (до INSERT) и кэш не вызывает LLM; требуют буферизации/сервисных доработок | KI-046 |
| `X-Trace-Id` на необработанных 500 | Starlette `ServerErrorMiddleware` вне пользовательских middleware | KI-047 |
| Партиционирование/retention `llm_calls`, `usage_daily` | MVP: таблица append-only с BRIN; партиции и агрегаты — отдельный шаг | KI-048 |
| Дашборды p50/p95/cost (T-109) | Нужен OTel-бэкенд/Grafana; в итерации — спаны и durable-данные | T-109 |
| Реальный LoRA-прогон (KI-030) | Нет GPU; schema v2 готова | KI-030 |

### Что дальше

- **KI-046:** охватить warmup, связать `commentary_id`/`cache_hit`.
- **KI-047/KI-048:** `X-Trace-Id` на 500; retention/партиционирование и
  агрегаты `usage_daily`.
- **T-109:** OTel-дашборды (p95 latency, error rate, tokens/cost) в
  Grafana/Tempo по `llm_calls`/спанам.
- **KI-030/T-104b:** LoRA на GPU (`schema_version=2`) + eval.

## Итерация 28 — FinOps Durable и Admin Export (T-031b, T-034b, KI-046, KI-047)

**Дата:** 2026-10-29. **Статус:** ✅ завершена. KI-046/KI-047 закрыты;
T-031b/T-034b закрыты в объёме MVP; заведены KI-049/KI-050.

### Задачи

- [x] **T-031b, durable `usage_daily`.** Миграция `e9f0a1b2c3d4` (head):
  subject_type/subject_key/day, tokens_in/out, cost_usd, requests,
  `UNIQUE(subject_type, subject_key, day)`, индексы `(day, subject_type)`
  и `(subject_type, subject_key, day)`, CHECK ≥ 0. Модель `UsageDaily`.
- [x] **T-031b, upsert.** `record_token_usage(..., cost_usd=)` после
  Redis-инкремента best-effort upsert-ит дельту в фоне (отдельная
  SessionLocal, `ON CONFLICT DO UPDATE`: токены/стоимость прибавляются,
  `requests+1`); `wait_for_pending_usage_upserts()` для тестов; сбой БД —
  только warning.
- [x] **T-031b, `/usage` и месячный скрипт.** `/v1/users/me/usage`:
  Redis + durable fallback (`used = max(redis, db)` при обоих источниках,
  DB при пустом/недоступном Redis); `scripts/aggregate_usage.py`
  (`--month`, `--json`, `--retention-days`, `--no-cleanup`, exit 0/2).
- [x] **KI-046, охват `llm_calls`.** Warmup в `llm_call_context
  (operation="warmup")`; `attach_commentary_id()` после commit
  комментария (sync+SSE) обновляет `commentary_id`; cache-hit
  commentary/chat пишут `cache_hit=true`, `provider="cache"`,
  `tokens/cost=0`, latency lookup.
- [x] **KI-047, `X-Trace-Id` на 500.** App-level `Exception` handler:
  500 `{"detail": "Internal Server Error", "code": "internal_error"}` +
  `X-Trace-Id` из `request.state.trace_id`; structlog
  `unhandled_exception`; `AppError` не затронут.
- [x] **T-034b, экспорт в админке.** `GET /v1/admin/exports/edits.jsonl`:
  NDJSON-стрим keyset-батчами, `Content-Disposition`;
  JWT admin/auditor (user 403, без кредов 401) + cookie `admin_session`;
  фильтры date_from/date_to/level/human_edited/schema_version (1|2);
  v2 — пакет фактов из `scripts/export_dataset`, v1 — legacy.
  Страница `/admin/exports/dataset` (`DatasetExportView`) с формой.
- [x] **Документация:** README (статус/таблицы/FinOps/Observability/
  KI), `KNOWN_ISSUES` (KI-046/047 → Closed, +KI-049/050),
  `TODO` (T-031b/T-034b → `[x]`), `CHANGELOG`, `RELEASES`,
  `ITERATION-28.md`.
- [x] **KI-051, production import (найдено верификатором).**
  `app/api/v1/admin_exports.py` импортировал `scripts.export_dataset`, а
  wheel/образ содержат только `app` → `uvicorn` падал. Builder'ы
  вынесены в `app/dataset/export.py` (скрипт реэкспортирует их), API
  импортирует только `app`; в CI docker-job добавлен smoke
  `docker run --rm chessannotator:ci python -c "import app.main"`.
  Проверено эмуляцией wheel-каталога без `scripts/`.

### Метрики

- **Backend-тесты:** **641 passed, 1 skipped, 1 xfailed** (56 файлов;
  +49 к итерации 27), **315.15 с**, exit 0. Skip — нет
  `DEEPSEEK_API_KEY`, xfail — KI-012.
- **Frontend-тесты:** **42 passed** (6 файлов); typecheck 0; build 6.92 с
  (JS 464.31 КБ / gzip 146.15, CSS 14.29 КБ / gzip 3.77).
- **E2E mocked:** **10 passed** (5 сценариев × chromium/mobile).
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 74 файла.
- **Alembic:** head `e9f0a1b2c3d4`; upgrade/downgrade-цикл и
  `alembic check` — чистые (14 таблиц, 10 ревизий).
- **FinOps smoke:** реальный `/v1/chat` (LM Studio) → `usage_daily`
  `user:212` tokens 818/44, requests 1; после удаления Redis-ключа
  `/usage` вернул `used_tokens=862` из БД; `aggregate_usage.py --json` —
  month 2026-10, tokens 2442/588, requests 2.
- **Админ-экспорт smoke:** JWT admin/auditor → 200 NDJSON (1 строка v2,
  `schema_version=2`, `user` начинается с «# Партия»), user → 403,
  без кредов → 401; cookie-сессия после `/admin/login` → 200;
  страница `/admin/exports/dataset` → 200 (admin/auditor);
  `Content-Disposition: attachment; filename="edits.jsonl"`,
  `Content-Type: application/x-ndjson`, `X-Trace-Id` присутствует.
- **KI:** 51 запись, 25 Open, 26 Closed.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Месячные лимиты/алерты 80/100%, reserve/reconcile, дашборд расходов | MVP T-031b закрывает durable-учёт и fallback; лимиты/алерты — отдельный шаг | KI-049, T-109 |
| Планировщик месячной агрегации/retention | Скрипт есть (`aggregate_usage.py`), cron/Temporal — инфраструктура | KI-049 |
| Retention/партиционирование `llm_calls` | Требует изменения уникального ключа и фоновой ротации | KI-048 |
| Фоновая выгрузка >5 с, checksum/размер, аудит выгрузки | DoD итерации — рабочий UI-экспорт с фильтрами; большие объёмы — отдельно | KI-050 |
| Реальный LoRA-прогон (KI-030) | Нет GPU; schema v2 готова | KI-030 |

### Что дальше

- **KI-049:** outbox/ARQ для durable-upsert (at-least-once), cron-агрегация
  и алерты 80/100%.
- **KI-048/KI-050:** партиционирование/retention `llm_calls`; аудит и
  фоновая выгрузка датасета.
- **T-109:** OTel-дашборды (p95 latency, error rate, tokens/cost) в
  Grafana/Tempo по `llm_calls`/спанам.
- **KI-030/T-104b:** LoRA на GPU (`schema_version=2`) + eval.

## Итерация 29 — LLMOps Dashboard и Retention (T-109, KI-048, KI-050)

**Дата:** 2026-10-29. **Статус:** ✅ завершена. **KI-048 закрыт**
(retention `llm_calls`); **T-109 закрыт** (визуальный LLMOps-дашборд в
админке); **KI-050 частично** (аудит выгрузки датасета). Заведён KI-052
(партиционирование `llm_calls`).

### Задачи

- [x] **KI-048, retention `llm_calls`.** `scripts/prune_llm_calls.py`:
  батчевый `DELETE ... WHERE id IN (SELECT id ... ORDER BY id LIMIT N)` в
  цикле (commit на батч, пауза `--sleep-ms`), настройка
  `LLM_CALLS_RETENTION_DAYS` (90 по умолчанию); флаги `--retention-days`,
  `--batch-size`, `--max-batches`, `--sleep-ms`, `--dry-run`, `--json`,
  exit code 0/2. Cron/systemd/Task Scheduler — в `GETTING_STARTED.md` §9.1.
  Партиционирование не делалось (опционально в постановке) — KI-052.
- [x] **T-109, LLMOps-дашборд.** `/admin/llmops`
  (`LlmOpsDashboardView`, `app/llmops_dashboard.py`, Jinja2 + Bootstrap):
  сводка за 24 часа / 7 / 30 дней — latency p50/p95/p99 и avg (только
  реальные вызовы, `cache_hit=false`), error rate (`error`/`timeout`),
  cache-hit rate, токены/стоимость с разбивкой по `model`/`level`;
  виджет «Топ-5 пользователей по токенам» из `usage_daily`. Read-only,
  admin/auditor.
- [x] **KI-050, аудит экспорта.** После завершения NDJSON-стрима
  `GET /v1/admin/exports/edits.jsonl` пишет в `audit_logs`
  `action="dataset.export"`: актор (`jwt:`/`session:`), фильтры
  (date/level/human_edited/schema_version), фактическое число строк,
  `completed` (false при обрыве). Best-effort в отдельной сессии.
- [x] **Документация:** `GETTING_STARTED` §9 (cron/retention),
  `DESIGN_ADMIN_AND_OPS` §4.4/§7.4, `ARCHITECTURE` §2, `README`
  (671 тест, дашборд, KI 52/25/27), `KNOWN_ISSUES` (KI-048 → Closed,
  KI-050 обновлён, +KI-052), `TODO` (T-109 → `[x]`), `CHANGELOG`,
  `RELEASES`, `ITERATION-29.md`.

### Метрики

- **Backend-тесты:** **671 passed, 1 skipped, 1 xfailed** (60 файлов;
  +30 к итерации 28), **326.67 с**, exit 0. Skip — нет
  `DEEPSEEK_API_KEY`, xfail — маркер KI-012.
- **Новые тесты:** retention — 9 unit + 4 integration; дашборд — 6 unit +
  7 integration; аудит экспорта — +4 (файл 15).
- **Frontend-тесты:** **42 passed** (6 файлов); typecheck 0; build 4.77 с
  (JS 464.31 КБ / gzip 146.15, CSS 14.29 КБ / gzip 3.77).
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 75 файлов.
- **Alembic:** новых миграций нет, head `e9f0a1b2c3d4` (14 таблиц,
  10 ревизий); партиционирование — KI-052.
- **Retention smoke (живая БД):** `--dry-run` → `deleted: 3`; реальный
  запуск `--batch-size 2` → `deleted: 3, batches: 2, capped: false`;
  повторный `--dry-run` → `0`; старых smoke-строк 0, свежих 2.
- **Дашборд smoke (uvicorn :8017, X-Admin-Token):** `/admin/llmops` —
  200, 54 352 байта; в HTML есть `LLMOps-дашборд`, `smoke-model`, p95,
  «Топ-5 пользователей».
- **Аудит экспорта smoke (JWT admin):** экспорт `edits.jsonl?level=club&
  schema_version=1` → 200 `application/x-ndjson`, 1 строка; в
  `audit_logs`: `jwt:smoke-admin-iter29@example.com | dataset.export |
  edits.jsonl | {"rows":1,"level":"club","schema_version":1,
  "completed":true,"trace_id":"5d65be9c-…",...}`.
- **KI:** 52 записи, 25 Open, 27 Closed.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Партиционирование `llm_calls` по месяцам | Требует смены `UNIQUE(trace_id, span_id)` на `(trace_id, span_id, created_at)` и пересоздания таблицы; в постановке итерации — опционально | KI-052 |
| Grafana/Tempo-дашборды и алерты аномалий стоимости | Нужен внешний OTel-бэкенд; в итерации 29 визуализация сделана внутри админки | T-113 |
| Фоновая выгрузка датасета >5 с, checksum/размер | Аудит выгрузки закрыт; остальные пункты KI-050 — отдельный шаг | KI-050 |
| Планировщик retention из коробки | Скрипт готов, cron/systemd/Task Scheduler описаны; в docker-compose сервиса-планировщика нет | GETTING_STARTED §9 |
| Реальный LoRA-прогон (KI-030) | Нет GPU | KI-030 |

### Что дальше

- **KI-052:** партиционированная `llm_calls` (RANGE по `created_at`,
  уникальный ключ с `created_at`) + pg_partman/cron-партиции.
- **KI-050:** инкрементальный v2-датасет/фоновая выгрузка, checksum.
- **T-113/T-109 (остаток):** Grafana/Tempo-дашборды и алерты аномалий.
- **KI-049:** outbox/ARQ для durable-upsert, cron-агрегация и алерты
  80/100%.

## Итерация 30 — PDF Export и Engine Cache (T-107, T-108)

**Дата:** 2026-10-29. **Статус:** ✅ завершена. **T-107 закрыт** (PDF-экспорт
комментария + кнопка в UI); **T-108 закрыт** (кэш результатов Stockfish).
Новых KI нет.

### Задачи

- [x] **T-108, engine cache.** Миграция `19bf1fdc2a73`: таблица
  `engine_cache` (`fen_hash`, `engine_version`, `depth`, `multipv`,
  `lines` JSONB, `created_at`; `UNIQUE(fen_hash, engine_version, depth,
  multipv)`, индекс `created_at`, CHECK depth/multipv ≥ 1).
  `app/engine/cache.py`: `EngineCache` (lookup/store/cleanup_expired,
  best-effort, отдельные сессии), нормализация FEN без номера хода,
  sha256-ключ. `EngineService.analyze` проверяет кэш до поиска и пишет
  результат после; `engine_version` — UCI `id name` (Stockfish 19),
  key включает depth/multipv (beginner 12/1 ≠ gm 28/5). Настройки
  `ENGINE_CACHE_ENABLED` / `ENGINE_CACHE_TTL_DAYS=30`, чистка при старте,
  кэш передаётся во все `EngineService` пула.
- [x] **T-107, PDF-экспорт.** `GET /v1/commentaries/{id}/export/pdf`:
  fpdf2 + TTF-шрифт с кириллицей (auto Arial/DejaVuSans, `PDF_FONT_PATH`),
  шапка (игроки, турнир, дата, результат, уровень, модель, статус),
  PGN-ходы, текст комментария с абзацами, нумерация страниц;
  `Content-Disposition: attachment; filename="commentary-{id}.pdf"`;
  JWT + изоляция данных (чужая запись → 404), `asyncio.to_thread` для
  CPU-bound генерации, битый legacy-PGN не ломает экспорт. Кнопка
  «Скачать PDF» в панели комментария (RU/EN), API-хелпер с single-flight
  refresh. В Docker-образ добавлен `fonts-dejavu-core`.
- [x] **Документация:** SPEC §4/§5 (`engine_cache`, PDF endpoint),
  DESIGN §8.1 (реализация engine cache), ARCHITECTURE §2–§5,
  USER_GUIDE (PDF), GETTING_STARTED (настройки + troubleshooting 8.10),
  README (696 тестов, 15 таблиц, head `19bf1fdc2a73`), TODO (T-107/T-108
  → `[x]`), CHANGELOG, RELEASES, `ITERATION-30.md`.
- [x] **Зависимости:** `fpdf2>=2.8` (production, чистый Python),
  `pypdf>=5.0` (dev, проверка PDF в тестах); обоснование — отчёт §7.

### Метрики

- **Backend-тесты:** **696 passed, 1 skipped, 1 xfailed** (64 файла;
  +25 к итерации 29), **238.43 с**, exit 0. Skip — нет
  `DEEPSEEK_API_KEY`, xfail — маркер KI-012.
- **Новые тесты:** engine cache — 6 unit + 6 integration (включая
  реальный Stockfish); PDF — 6 unit + 6 integration; схема БД — +1.
- **Frontend-тесты:** **46 passed** (7 файлов; +4 PDF-API); typecheck 0;
  build 4.95 с (JS 465.21 КБ / gzip 146.45, CSS 14.29 КБ / gzip 3.77).
- **E2E mocked:** **10 passed** (5 сценариев × chromium/mobile).
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 78 файлов.
- **Alembic:** 11 ревизий, head `19bf1fdc2a73`; upgrade + `alembic check`
  + roundtrip `downgrade base → upgrade head` — чистые; 15 таблиц.
- **Engine cache smoke (живой uvicorn :8018, real Stockfish):**
  `POST /v1/analyze` (4 полухода, beginner) — **656 мс → 22 мс** при
  повторе (×30); в `engine_cache` 5 строк (`engine_version=Stockfish 19`,
  depth=12, multipv=1).
- **PDF smoke:** `GET /v1/commentaries/1/export/pdf` → 200,
  `application/pdf`, `attachment; filename="commentary-1.pdf"`,
  32 846 байт; pypdf извлёк игроков, турнир, ходы и текст комментария.
- **KI:** 52 записи, 25 Open, 27 Closed (без изменений).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Диаграмма доски в PDF | В постановке «возможно» (опционально); для PNG нужен растровый рендер SVG (cairo/native), а `chess.svg` + fpdf2 SVG-поддержка не гарантируют качество | — |
| DOCX-экспорт | В DoD только PDF; отдельная фича | — |
| Кэш retrieval по `fen_hash` (`retrieval_cache`) | T-108 в backlog означал другой кэш; выполнен engine cache из постановки итерации; retrieval-кэш остаётся частью T-018/T-032b | T-018, T-032b |
| Redis hot-слой для engine cache | Для MVP достаточно Postgres; Redis-слой — оптимизация при росте нагрузки | T-032b (по аналогии) |
| Реальный LoRA-прогон (KI-030) | Нет GPU | KI-030 |

### Что дальше

- **T-107 остаток:** диаграмма критической позиции в PDF и/или DOCX.
- **T-108 остаток:** метрики cache hit/miss в админке/OTel, Redis hot-слой.
- **T-032b:** Redis hot-слой semantic cache и метрики.
- **KI-049/KI-052/KI-050:** планировщик FinOps, партиционирование `llm_calls`, фоновая выгрузка датасета.

---

## Итерация 31 — Observability Stack & PDF Board Diagram (T-109b, T-107b, KI-050)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. T-107b, T-109b закрыты; KI-050 закрыт,
добавлен KI-053.
**Отчёт:** `docs/development/reports/v0.8.0/ITERATION-31.md`

### Что сделано

- [x] **T-107b, диаграмма доски в PDF.** `app/export/board.py`:
  `chess.svg.board()` → PNG через `cairosvg`; финальная позиция + до
  двух критических моментов (`blunder`/`mistake` по `|delta_cp|`) с
  подсветкой последнего хода. PDF вставляет PNG (82 мм, подпись),
  pypdf видит image-XObject. Без `cairosvg`/`libcairo2` — graceful
  degradation (PDF без картинки, не 500). `libcairo2` добавлен в
  `Dockerfile` и CI; `cairosvg>=2.7` — production-зависимость.
- [x] **T-109b, observability-стек.** `docker-compose.observability.yml`:
  `otel-collector` (OTLP + spanmetrics), `tempo`, `prometheus`,
  `grafana` (порты loopback, provisioning datasource'ов и дашборда).
  `app/observability.py` экспортирует OTLP-метрики
  `llm.calls/llm.tokens/llm.cost.usd/llm.latency`; HTTP-спан несёт
  `http.route`/`http.response.status_code`; нативная телеметрия
  FastAPI 0.162 отключена, чтобы не дублировать спаны. Дашборд
  `chessannotator-observability` (9 панелей): p95/p99 для
  `/v1/commentary` и `/v1/analyze`, 5xx error rate, стоимость, токены,
  LLM-латентность по моделям.
- [x] **KI-050, фоновая выгрузка датасета.** Миграция `b0c9d2f13425`
  (`export_jobs`), `POST /v1/admin/exports/jobs` (202) →
  BackgroundTasks → NDJSON в `EXPORT_DIR` (вне web-root) →
  `GET /jobs/{id}/download`; статусы queued/running/done/failed,
  checksum/размер/rows, атомарная запись, аудит `dataset.export` с
  `job_id`. В админке — форма «Создать экспорт», поллинг и кнопка
  «Скачать»; создание только admin/legacy, auditor read-only.
- [x] **Документация:** SPEC §4/§5/§10, ARCHITECTURE §2/§4/§5/§5.1,
  GETTING_STARTED §6.2/§8.11/§9.3, USER_GUIDE (диаграммы, §7.9),
  DESIGN_ADMIN_AND_OPS §5.4/§6.4, README, TODO, CHANGELOG,
  KNOWN_ISSUES, этот отчёт.

### Метрики

- **Backend-тесты:** **724 passed, 1 skipped, 1 xfailed** (68 файлов;
  +28 к итерации 30), **252.64 с**, exit 0. Skip — нет cairo на
  Windows-dev; xfail — маркер KI-012.
- **Новые тесты:** диаграммы — 9 (8+skip), фоновые выгрузки — 9,
  OTLP-метрики — 5, HTTP-спан — 3, PDF integration — +1, схема — +1.
- **Frontend-тесты:** **46 passed** (7 файлов); typecheck 0;
  build 6.74 с (JS 465.21 КБ / gzip 146.45, CSS 14.29 КБ / gzip 3.77).
- **E2E mocked:** **10 passed** (5 сценариев × chromium/mobile), 5.6 с.
  Локально — через `vite preview` из-за sandbox-ограничения esbuild
  (см. отчёт §5); CI использует `pnpm dev`.
- **ruff:** 0 ошибок. **mypy:** 0 ошибок (strict), 81 файл.
- **Alembic:** 12 ревизий, head `b0c9d2f13425`; upgrade + `alembic
  check` + roundtrip `downgrade base → upgrade head` — чистые;
  16 доменных таблиц.
- **Observability smoke:** 4/4 контейнера healthy; Grafana — 9 панелей,
  datasources Prometheus/Tempo; Prometheus targets 4/4 up; p95
  `/v1/analyze` = **242.5 мс**; OTLP-метрики `llm_calls_total=1`,
  `llm_tokens_total=1801`, `llm_cost_usd_total=0.000957`; Tempo хранит
  трейс (`X-Trace-Id` → 1 span).
- **PDF smoke (Docker, cairo):** PNG 33 533 Б, PDF 77 952 Б, 1 страница,
  1 image; pypdf извлёк «Позиции на доске», «Финальная позиция»,
  игроков и комментарий. Docker image `chessannotator:iter31` собирается,
  `import app.main` и `cairosvg_available()=True`.
- **KI:** 53 записи, 25 Open, 28 Closed (KI-050 закрыт, +KI-053).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Джоба переживает рестарт / arq-очередь | MVP на BackgroundTasks; очередь, recovery и retention файлов — отдельный шаг | KI-053 |
| Подписанные ссылки на скачивание | Не входили в DoD; доступ по JWT/cookie админки | — |
| Диаграммы со стрелками/вариантами, ориентация по игроку | Объём T-107b — финальная позиция + 2 критических | — |
| Production-профиль observability (S3/HA/TLS/SSO) | Dev/staging-стек; алерты — T-113 | T-113 |
| Реальный LoRA-прогон (KI-030) | Нет GPU | KI-030 |

### Что дальше

- **KI-053:** перевести фоновые выгрузки на `arq` (Redis-очередь),
  добавить recovery зависших `running`, retention `EXPORT_DIR`/
  `export_jobs`, подписанные ссылки.
- **T-113:** Alertmanager-алерты по стоимости/latency/error rate на
  основе дашборда и метрик.
- **T-107b остаток:** стрелки/варианты на диаграммах, диаграммы по
  фазам, кэш PNG.
- **T-109b остаток:** production-профиль Tempo/Prometheus (S3,
  remote write), datasource'ы с TLS/SSO.

---

## Итерация 32 — Auth Finalization & ARQ Worker (KI-041, KI-053)

**Дата:** 2026-10-29
**Статус:** ✅ завершена. KI-041 и KI-053 закрыты; добавлены KI-054,
KI-055.
**Отчёт:** `docs/development/reports/v0.9.0/ITERATION-32.md`

### Что сделано

- [x] **KI-053, ARQ-воркер.** `app/worker.py` + корневой `worker.py`
  (`WorkerSettings`, `run_export_job(ctx, job_id)`); API только
  `enqueue_job` в Redis (503 `export_queue_unavailable` + `failed` при
  недоступной очереди); recovery `running >15 мин → failed` при старте
  API/воркера; сервис `worker` в `docker-compose.yml`/
  `docker-compose.prod.yml` с общим volume; `Dockerfile` копирует
  `worker.py`.
- [x] **KI-041, email verification.** Миграция `dcf1da1421cd` —
  `users.is_verified` (legacy true, регистрация false);
  `app/email/sender.py` (Protocol + MockEmailSender/outbox);
  `/auth/register` шлёт письмо, `GET /auth/verify-email` ставит флаг;
  `/v1/analyze` и `/v1/commentary` → 403 `email_not_verified`.
- [x] **KI-041, сброс пароля.** `POST /auth/forgot-password` (202 без
  enumeration) и `POST /auth/reset-password` (Argon2id, одноразовый
  токен, глобальный отзыв refresh, clear cookie).
- [x] **KI-041, logout-all.** `POST /auth/logout-all` пишет
  `revoked_before:<user_id>` (Unix-ms); `/auth/refresh` сверяет
  `iat_ms`/`issued_at` и отклоняет старые refresh.
- [x] **Тесты и документация:** см. отчёт; SPEC/ARCHITECTURE/
  GETTING_STARTED/USER_GUIDE/DESIGN/README, TODO, CHANGELOG,
  KNOWN_ISSUES.

### Метрики

- **Backend-тесты:** **746 passed, 1 skipped, 1 xfailed** (70 файлов;
  +22 к итерации 31), **260.87 с**, exit 0. Skip — cairo на Windows-dev;
  xfail — KI-012.
- **Новые тесты:** ARQ — 12 (было 9), auth KI-041 — 8, email/tokens —
  5, global revocation — +4, схема — +1.
- **Frontend-тесты:** **46 passed**; typecheck 0; build 6.92 с
  (JS 465.21 КБ / gzip 146.45, CSS 14.29 КБ / gzip 3.77).
- **E2E mocked:** **10 passed** (chromium/mobile), 7.3 с; локально —
  `vite preview` (sandbox esbuild), CI — `pnpm dev`.
- **ruff:** 0; **mypy:** 0 (strict), 84 файла.
- **Alembic:** 13 ревизий, head `dcf1da1421cd`; upgrade + `alembic
  check` + roundtrip — чистые; 16 таблиц.
- **ARQ smoke:** Docker worker (dev) — `run_export_job` job 10:
  `done`, rows=1, size=11660, sha256 `320b3b24…`, 0.33 с; recovery
  count=0; `arq worker.WorkerSettings --burst` — 1 function, exit 0.
- **Auth live smoke (:8020):** register `is_verified=False`; analyze
  без verify → 403 `email_not_verified`; verify True; analyze id=18;
  forgot 202; reset 200; старый пароль 401; новый 200; logout-all 204.
- **Docker image:** `chessannotator:iter32` собирается, `import
  app.main, worker` OK, `cairosvg_available()=True`.
- **KI:** 55 записей, 25 Open, 30 Closed (KI-041/KI-053 закрыты,
  +KI-054/KI-055).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Реальный SMTP/SES | Mock достаточно для dev/CI; нужен провайдер/секреты | KI-055 |
| Frontend-страницы verify/reset, resend | DoD итерации 32 — backend-flow | KI-055 |
| 2FA/TOTP, SSO, лимит попыток по email | Отдельный объём | KI-055 |
| Retention файлов `EXPORT_DIR`/`export_jobs`, signed URL | Не входили в DoD очереди | KI-054 |
| Мгновенный отзыв access-токенов | JWT stateless; access живёт 15 мин | — |
| Heartbeat/updated_at для длинных выгрузок | Нет колонки; recovery по `started_at` | KI-054 |
| Реальный LoRA-прогон (KI-030) | Нет GPU | KI-030 |

### Что дальше

- **KI-055:** SMTP/SES, frontend verify/reset/resend, TOTP/SSO, лимит
  попыток по (email, IP).
- **KI-054:** retention export-файлов/строк, signed URL, heartbeat
  длинных джоб.
- **T-113:** Alertmanager по стоимости/latency/error rate.
- **T-118:** SSO/SAML для админки.

---

## Итерация 33 — Frontend Auth UX & Export Retention (KI-055, KI-054)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. KI-055 и KI-054 закрыты (в объёме итерации);
добавлены KI-056 (SMTP/2FA/SSO/лимит по email/resend) и KI-057
(signed URL).
**Отчёт:** `docs/development/reports/v0.10.0/ITERATION-33.md`

### Что сделано

- [x] **KI-055, verify-email UI.** Публичный `/verify-email?token=…`:
  `GET /auth/verify-email` при монтировании, статусы «Идёт проверка…» /
  «Email успешно подтвержден!» / ошибка; single-flight на токен
  (StrictMode); обновление профиля в `authStore` после успеха.
- [x] **KI-055, forgot/reset UI.** `/forgot-password` (нейтральный 202) и
  `/reset-password?token=…` (новый пароль + повтор, редирект на `/login`
  с сообщением); ссылка «Забыли пароль?» на `/login`; i18n RU/EN.
- [x] **KI-055, unverified gate.** `AuthGate` показывает «Подтвердите
  email» при `is_verified=false` (admin/auditor bypass), кнопка повторной
  проверки профиля; `HomePage` обрабатывает 403 `email_not_verified`.
- [x] **KI-055, logout-all.** Выпадающее меню профиля в шапке: «Выйти» и
  «Выйти со всех устройств» (`POST /auth/logout-all`, очистка store,
  редирект на `/login`).
- [x] **Ссылки писем.** Ведут на SPA (`APP_BASE_URL`/`verify-email`,
  `/reset-password`), dev-дефолт `http://localhost:5173`; `APP_BASE_URL`
  в prod-compose переопределяется.
- [x] **KI-054, retention.** `scripts/prune_export_jobs.py` — батчевое
  удаление старых строк `export_jobs` и файлов внутри `EXPORT_DIR`;
  `EXPORT_RETENTION_DAYS=7`; `--dry-run`/`--json`/`--max-batches`;
  защита от путей вне каталога; cron/systemd/Windows Task Scheduler в
  `GETTING_STARTED.md` §9.3; `scripts/` копируется в Docker-образ.
- [x] **Тесты и документация:** Vitest 53 (+7), Playwright 28 (+18),
  backend 761 (+15); SPEC/ARCHITECTURE/GETTING_STARTED/USER_GUIDE/
  DESIGN/README/TODO/CHANGELOG/KNOWN_ISSUES.

### Метрики

- **Backend-тесты:** **761 passed, 1 skipped, 1 xfailed** (72 файла;
  +15 к итерации 32), **241.81 с**, exit 0. Skip — cairo на Windows-dev;
  xfail — KI-012.
- **Новые тесты:** retention 15 (9 unit + 6 integration), auth-flow API 7,
  auth-UX E2E 18 (9×2); обновлены ссылки/моки auth.
- **Frontend-тесты:** **53 passed** (8 файлов); typecheck 0; build 5.60 с
  (JS 480.87 КБ / gzip 149.85, CSS 15.70 КБ / gzip 4.09).
- **E2E mocked:** **28 passed** (14 сценариев × chromium/mobile), 23.0 с;
  локально на Windows/DSH — `E2E_PREVIEW=1` (build+preview), CI —
  `pnpm dev`.
- **ruff:** 0; **mypy:** 0 (strict), `app` 84 файла + скрипт.
- **Alembic:** 13 ревизий, head `dcf1da1421cd`; upgrade + `alembic check`
  + roundtrip — чистые; 16 app-таблиц (+`alembic_version`).
- **Docker:** `chessannotator:iter33` собирается; `import app.main,
  worker, scripts.prune_export_jobs` OK; `cairosvg_available()=True`.
- **Retention smoke:** `--dry-run --json` на iter33-БД → `deleted_rows=0`,
  `deleted_files=0`, exit 0.
- **KI:** 57 записей, 25 Open, 32 Closed (KI-054/KI-055 закрыты,
  +KI-056/KI-057).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Реальный SMTP/SES | Mock достаточно для dev/CI; нужен провайдер/секреты | KI-056 |
| Resend verification | Нет endpoint и cooldown; письмо живёт 24 ч | KI-056 |
| 2FA/TOTP, SSO, per-email лимит | Отдельный объём | KI-056/T-118 |
| Signed URL экспортов | Требует TTL/scope-дизайна; retention уже закрыт | KI-057 |
| Мгновенный отзыв access-токенов | JWT stateless; access живёт 15 мин | — |
| Real-backend E2E после verify | Новый пользователь unverified; нужен доступ к outbox/логу | KI-040/056 |
| Авто-retry упавших retention-файлов | Ошибка удаления логируется, строка чистится | KI-054 §5 |
| LoRA-обучение (KI-030) | Нет GPU | KI-030 |

### Что дальше

- **KI-056:** SMTP/SES, resend-verification, 2FA/TOTP, SSO, per-email
  rate limit.
- **KI-057:** signed URL для `/v1/admin/exports/jobs/{id}/download`
  (TTL + scope + аудит).
- **T-113:** Alertmanager по стоимости/latency/error rate.
- **T-118:** SSO/SAML для админки.

---

## Итерация 34 — Release Prep: Security, GHCR, EN Localization & Alerts (T-110, T-027, T-106, T-113)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. T-110 и T-106 закрыты, T-027/T-113
реализованы; новый KI-058 (English localization ограничена
комментариями).
**Отчёт:** `docs/development/reports/v0.11.0/ITERATION-34.md`

### Что сделано

- [x] **T-110, dependency audit.** `pip-audit` нашёл python-jose 3.5.0
  (PYSEC-2026-4298) и ecdsa 0.19.2 (PYSEC-2026-1325); оба заменены
  переходом на **PyJWT** (HS256). После миграции — «No known
  vulnerabilities found».
- [x] **T-110, конфиг.** `Settings` падает в `APP_ENV=prod/production`
  на dev-`JWT_SECRET`/ключе <32 символов и dev-`ADMIN_API_KEY`;
  `CORS_ORIGINS=*` запрещён всегда (allow_credentials=True). Тесты:
  `tests/unit/test_config_security.py` (10).
- [x] **T-110, SQL-инъекции.** Проверены `app/llmops_dashboard.py`
  (ORM select с параметрами), `app/dataset/jobs.py` и
  `app/api/v1/admin_exports.py` (ORM update/delete, FileResponse,
  path-containment); raw SQL только статический advisory-lock в
  `app/audit.py`. Пользовательский ввод в SQL не интерполируется.
- [x] **T-027, GHCR.** Job `publish-docker` в CI: только тег `v*`,
  `docker/login-action` + `GITHUB_TOKEN`, metadata-теги
  `vX.Y.Z`/`X.Y.Z`/`latest`, build-push-action с gha-cache; build-arg
  `VITE_API_BASE`.
- [x] **T-106, English prompts.** `beginner_en.txt`, `club_en.txt`,
  `gm_en.txt`; `DEFAULT_LANGUAGE`; `language` в `/v1/commentary` и
  `/stream` (fallback `Accept-Language`); английская retry-подсказка;
  frontend передаёт `i18n.language`.
- [x] **T-113, Grafana alerts.** `HighErrorRate`, `HighLatency`,
  `LLMBudgetExceeded` в `deploy/grafana/provisioning/alerting/rules.yaml`;
  проверено локально через Grafana API (3 правила, папка
  ChessAnnotator).
- [x] **Тесты/документация:** Vitest 55 (+2), E2E 28, backend 779 (+18);
  README/SPEC/ARCHITECTURE/GETTING_STARTED/USER_GUIDE/DESIGN/TODO/
  CHANGELOG/KNOWN_ISSUES.

### Метрики

- **Backend-тесты:** **779 passed, 1 skipped, 1 xfailed** (73 файла;
  +18 к итерации 33), **268.32 с**, exit 0. Skip — cairo на Windows-dev;
  xfail — KI-012.
- **Новые тесты:** security-конфиг 10, English prompts +6,
  English commentary +2, frontend +2.
- **Frontend-тесты:** **55 passed** (8 файлов); typecheck 0; build 5.77 с
  (JS 480.98 КБ / gzip 149.86, CSS 15.70 КБ / gzip 4.09).
- **E2E mocked:** **28 passed** (14 × 2), 28.4 с; локально
  `E2E_PREVIEW=1`, CI — `pnpm dev`.
- **pip-audit:** до — 3 уязвимости в 2 пакетах; после — 0.
- **ruff:** 0; **mypy:** 0 (strict), 84 файла.
- **Alembic:** схема не менялась, head `dcf1da1421cd`.
- **Grafana:** 3 правила, health `ok`, folderUID `chessannotator`.
- **KI:** 58 записей, 26 Open, 32 Closed (+KI-058).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Фактический GHCR push | Нужен `git tag v*` и registry-доступ; workflow готов | T-027 |
| Docker build в этой среде | Docker Hub auth timeout (TLS), не код | — |
| Уведомления Telegram/Slack | DoD допускал UI-only; contact point — отдельный шаг | T-113 |
| English chat/PDF/errors, en-промпты в БД | DoD T-106 — комментарии; нужна миграция `prompts.language` | KI-058 |
| Порог latency 10 с для локальной LLM | Соответствует постановке; в dev будет шуметь | §5 |
| `pnpm audit`/OS-аудит | pip-audit покрывает только Python | — |
| LoRA-обучение (KI-030) | Нет GPU | KI-030 |

### Что дальше

- **T-027:** создать тег `v1.0.0`, проверить `publish-docker`, сделать
  GHCR package public.
- **KI-058:** `prompts.language`, en-чат/PDF, вычитка промптов.
- **T-113:** contact point (Slack/Telegram) и пороги под production LLM.
- **T-111:** multi-tenant/RBAC.

---

## Итерация 35 — Final Polish & v1.0.0 Documentation (KI-058, T-024, T-106)

**Дата:** 2026-11-01
**Статус:** ⚠️ релиз v1.0.0 подготовлен; живой слепой тест (T-024) не
выполнен, тег/GHCR — действие мейнтейнера.
**Отчёт:** `docs/development/reports/v1.0.0/ITERATION-35.md`

### Что сделано

- [x] **KI-058, English для PDF.** `GET
  /v1/commentaries/{id}/export/pdf` принимает `language` (`ru`/`en`;
  иначе `Accept-Language` → `DEFAULT_LANGUAGE`); при `en` — английские
  заголовки и подписи секций (Game, Moves, Commentary, Positions on the
  board, Event, Date, Result, Level, Model, Status). PGN/SAN не зависят
  от языка.
- [x] **KI-058, язык чата.** `POST /v1/chat` и `/v1/chat/stream`
  принимают `language` (иначе `Accept-Language` → `DEFAULT_LANGUAGE`);
  английский системный промпт — `app/prompts/chat_en.txt`; retry-подсказка
  и ответ сохраняют язык.
- [x] **KI-058, локализация ошибок API.** `detail` доменных `AppError`
  выбирается по `Accept-Language` (`app/i18n.py`): при запросе `en` для
  известного `code` возвращается английский текст, иначе — исходное
  сообщение; `code` и HTTP-статус не меняются, формат
  `{"detail", "code"}` сохранён.
- [x] **KI-058 закрыт** в объёме v1.0.0; остатки (русский факт-пакет,
  en-промпты файлами без редактирования в админке, нет вычитки
  носителем) зафиксированы внутри записи KI-058, новых KI не заводилось.
- [x] **Web UX: `/history` для неподтверждённых.** Плейсхолдер
  «Подтвердите email, чтобы увидеть историю» вместо пустого списка при
  `is_verified=false` (admin/auditor bypass); i18n RU/EN.
- [x] **Admin: read-only `export_jobs`.** View в `/admin` — таблица
  фоновых выгрузок (status/rows/size/sha256/timestamps) с фильтром по
  `status`; создание и правка через view запрещены (admin/auditor).
- [x] **T-024: blind-test script.** `scripts/generate_blind_test.py` —
  3 партии с анализом × 3 уровня, слоты `A`/`B`/`C` перемешиваются
  засеянным `random.Random(seed)`; на выходе `game_N_X.txt`,
  `answer_form.md` (уровни не раскрыты) и `answers_key.json` для
  организатора; `--dry-run` работает без LLM, exit codes 0/2. Живой
  тест с людьми не проводился (см. ниже); в `TODO.md` T-024 помечен
  `[x]` с явной пометкой «скрипт готов, живой тест — вручную».
- [x] **Версия 1.0.0.** `pyproject.toml`, `app/__init__.py`,
  `web/package.json`; CHANGELOG `[1.0.0] - 2026-11-01`.
- [x] **Финальные документы.** README (v1.0.0, docker compose quick start:
  dev/prod/observability, обновлённая таблица статуса), CHANGELOG (весь
  Unreleased сгруппирован в `Added`/`Changed`/`Fixed`/`Security`,
  дублирующиеся заголовки убраны), ARCHITECTURE (C4 Context/Container,
  локализация, фоновые выгрузки, актуализация API/тестов/развёртывания),
  GETTING_STARTED (проверенные команды, раздел «Запуск тестов и E2E
  локально», LM Studio/DeepSeek, исправления устаревших инструкций),
  docs/README (отчёт v1.0.0 в индексе), KNOWN_ISSUES (KI-058 → Closed,
  сводка 25 Open / 33 Closed), TODO (T-024 и отметки итерации 35).

### Метрики

> Финальные числа итерации 35 — полный прогон на изолированной БД
> `chessannotator_iter35` (PostgreSQL 5435, Redis 6382, Stockfish 17).

- **Backend-тесты:** **869 passed, 3 skipped, 1 xfailed** (77 файлов,
  **338.69 с**, exit 0). Skip — 2 cairo-кейса на Windows-dev и
  `DEEPSEEK_API_KEY` не задан; xfail — KI-012.
- **Frontend-тесты:** **58 passed** (8 файлов); typecheck 0; build 6.74 с
  (JS 483.63 КБ / gzip 150.45, CSS 15.70 КБ / gzip 4.09).
- **E2E mocked:** **30 passed** (15 сценариев × 2, 21.5 с); локально
  `E2E_PREVIEW=1`, CI — `pnpm dev`.
- **ruff / mypy:** 0 / 0 (strict, 85 файлов); **alembic:** схема не
  менялась, head `dcf1da1421cd`.
- **Версия:** `1.0.0` (pyproject/app/web).
- **KI:** 58 записей, **25 Open, 33 Closed** (KI-058 закрыт).
- **Docker/GHCR:** образ не собирался и не публиковался в этой сессии —
  job `publish-docker` сработает по тегу `v*`.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Живой слепой тест уровней (10 шахматистов, ≥ 80%) | Нужны реальные участники; инструмент (`scripts/generate_blind_test.py`) готов | T-024 |
| Тег `v1.0.0` и публикация в GHCR | Действие мейнтейнера: `git tag v1.0.0 && git push origin v1.0.0`; workflow готов | T-027 |
| Docker build в этой сессии | В рамках документационной итерации не подтверждён; CI-job `docker` собирает образ и делает smoke `import app.main` | CI |
| Вычитка EN-промптов и текстов носителем | Нет native speaker'а в команде | KI-058 (residual c) |
| en-промпты в админке (`prompts.language`) | Требует миграции и UI; в v1.0.0 en — файлами | KI-058 (residual b) |
| Русский факт-пакет для en-генерации | Отдельный объём перевода фактов; LLM пересказывает ru-пакет | KI-058 (residual a) |
| LoRA-обучение (KI-030) | Нет GPU | KI-030 |
| Signed URL экспортов | Требует TTL/scope-дизайна | KI-057 |

### Что дальше

- **Релиз:** `git tag v1.0.0` + `git push origin v1.0.0`; проверить
  `publish-docker` и GHCR-теги `:v1.0.0`/`:1.0.0`/`:latest`.
- **T-024:** провести живой слепой тест с 10 шахматистами по материалам
  из `scripts/generate_blind_test.py`, оформить отчёт.
- **KI-058 residuals:** вычитка EN native speaker'ом, `prompts.language`
  (en-промпты в БД и админке), `commentaries.language` для аналитики.
- **KI-030:** качество LLM (промпты/обучение LoRA при появлении GPU).
- **KI-057:** signed URL для скачивания экспортов.
- **T-111…T-119:** multi-tenant/RBAC, SSO, backup/DR, k8s — Backlog.

## Итерация 36 — UX Polish & Localization (Feedback Fixes)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Пост-релизная полировка v1.0.0 по фидбэку
тестировщика: классические шахматные «фишки» в интерфейсе и баг
локализации мотивов. Версия остаётся **1.0.0**.

### Сделано

- [x] **Локализация мотивов.** 9 типов мотивов переведены (RU/EN) в
  `web/src/i18n.ts` (`motifs.*`); `AnalysisWorkspace` рендерит
  `t("motifs.<type>")` с fallback на исходный тег — `weak_square`/`pin`/
  `fork` больше не видны пользователю.
- [x] **Тултип лимитов.** `UsageBadge.title` = «Израсходовано токенов
  LLM: {{used}} из {{limit}} (дневной лимит)» с `toLocaleString` по языку
  (ru «9 300», en «9,300»); `usage.details` остался в модалке.
- [x] **EvalBar.** Вертикальная шкала оценки у доски: белые сверху,
  чёрные снизу, clamp ±1000 cp, `role="meter"`, подпись мата `M3`/`M-2`.
- [x] **EvalGraph.** Линейный график оценки (recharts 3.10.1) по
  полуходам, ось Y −1000…+1000, нулевая линия, маркер выбранного хода;
  клик по графику переключает доску.
- [x] **Навигация.** `NavigationControls` под доской: в начало / назад /
  вперёд / в конец; на границах партии кнопки недоступны; счётчик
  «полуход / всего».
- [x] **Скрытие оценок.** Чекбокс «Показывать оценки» в шапке списка
  ходов: скрывает `move__eval` и `cp` в `title`, классификация и цвет
  остаются.
- [x] **Экспорт PGN.** `buildAnnotatedPgn` (заголовки исходной партии,
  `Annotator`, `{оценка, классификация}` на каждый ход, санитайзинг
  скобок/переводов строк), `buildPgnFromSan` (fallback по SAN, FEN-старт),
  `downloadTextFile` (Blob) и кнопка «Скачать PGN».
- [x] **Бандл.** Route-level code-split `HomePage`/`AnalysisPage`
  (`React.lazy` + `Suspense`): основной чанк 112.10 kB gzip (было 255.26),
  recharts уехал в асинхронный чанк (RULES §3.2).
- [x] **Тесты.** +10 unit (`eval`), +13 unit (`pgn`), расширен
  `analyze.spec.ts` (шкала, график, навигация, клик, toggle) и новый
  `pgn-export.spec.ts` (9 подписей мотивов, tooltip лимита, download
  event, re-parse PGN).

### Метрики

- **Backend-тесты:** **869 passed, 3 skipped, 1 xfailed** (77 файлов,
  **337.03 с**, exit 0) — бэкенд в итерации не менялся.
- **Frontend-тесты:** **81 passed** (9 файлов; +23 к итерации 35);
  typecheck 0.
- **Сборка:** main chunk **112.10 kB gzip** (было 255.26 до split;
  лимит RULES §3.2 — 200 kB), async `analysisStore`-чанк 142.91 kB gzip,
  CSS 4.62 kB gzip, 9.68 с.
- **E2E mocked:** **32 passed** (16 сценариев × chromium/mobile), 32.2 с;
  добавлен `pgn-export`.
- **ruff / mypy:** 0 / 0 (85 файлов); **alembic:** схема не менялась.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Скачивание PGN с заголовками исходной партии | `GET /v1/games/{id}` не отдаёт PGN, `AnalysisPage` его не хранит; используется fallback по SAN (Event «ChessAnnotator», White/Black «?») | follow-up: отдавать PGN в ответе game |
| Real-backend E2E для новых сценариев | `E2E_REAL_BACKEND=1` требует живого бэкенда; проверено mocked | KI-040 |
| Ленивая загрузка `AnalysisWorkspace` внутри HomePage | Главная всегда рендерит workspace, поэтому тянет общий async-чанк 142.91 gzip; отдельная полировка бандла | бэклог |
| Живой слепой тест, тег v1.0.0, GHCR | Вне фокуса UX-итерации | T-024, T-027 |

### Что дальше

- Отдавать PGN в `GET /v1/games/{id}` и передавать его в
  `AnalysisWorkspace` — тогда «Скачать PGN» сохранит Event/игроков/ECO.
- Ленивая загрузка workspace на главной (догружать recharts только при
  наличии анализа) для дальнейшего уменьшения бандла.
- Продолжить T-024 (живой слепой тест) и пост-релизный бэклог
  (T-111…T-119).

## Итерация 37 — Interactive Board & Semantic Validation (T-070, KI-023)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Версия — **1.1.0**.

### Сделано

- [x] **T-070, интерактивная доска.** `Board.tsx` получил `interactive` и
  `onPieceDrop` (react-chessboard 5: `allowDragging`, `onPieceDrop` → bool);
  в `AnalysisWorkspace` появилось локальное состояние варианта:
  легальный ход создаёт `{fen, sans, uci}`, нелегальный откатывается.
  Над доской — строка «Вариант: 3. Ng5 Nd4» (`formatVariantLine`) и
  кнопка «Сбросить на партию»; вариант сбрасывается при навигации
  (`selectedPly`) и смене анализа. Чистые хелперы — `lib/variant.ts`
  (`applyUserMove`, `formatVariantLine`), 12 unit-тестов.
- [x] **T-071, загрузка PGN из файла.** Кнопка «Загрузить файл» (📎) рядом
  с PGN-полем: скрытый `input[type=file]`, `FileReader.readAsText`,
  лимит **1 МБ**, проверка расширения `.pgn` (регистр не важен), ошибки —
  в существующем баннере (`pgn.uploadTooLarge`/`uploadWrongType`/
  `uploadEmpty`/`uploadError`). Хелперы — `lib/pgnFile.ts`
  (`MAX_PGN_FILE_BYTES`, `validatePgnFile`, `readPgnFile`,
  `pgnFileErrorMessageKey`), 18 unit-тестов.
- [x] **KI-023, семантическая валидация LLM.** Новый
  `app/commentary/semantic.py`: `SemanticClaim`, `extract_semantic_claims`
  (RU+EN: «фигура на поле», «связка [цвета] [фигуры] [на] поле»,
  «pin of the f7 pawn», «the e6 bishop is pinned»),
  `validate_semantic_claims` (позиции: старт + mainline + PV-линии;
  утверждение валидно, если фигура стоит на поле хотя бы в одной позиции;
  для связки проверяется только существование фигуры),
  `strip_invalid_claims` (вырезание предложений с сохранением абзацев,
  никогда не возвращает пустой текст), `semantic_retry_prompt`.
  Интеграция в `_generate_with_validation`: retry при неверных фактах,
  на последней попытке — вырезание вместо 502; комбинированная подсказка
  (ходы + факты); structlog-счётчики без текста LLM. Флаг
  `semantic_validation_enabled` (env `SEMANTIC_VALIDATION_ENABLED`).
- [x] **Тесты.** Backend +50: `tests/unit/test_semantic_validation.py`
  (43), `tests/integration/test_commentary_semantic.py` (7, DB-backed с
  scripted LLM: retry, strip на последней попытке, флаг off,
  комбинированный промпт). Frontend +30: `variant` (12), `pgnFile` (18).
  E2E +5 сценариев: `board-variant` (реальное перетаскивание мышью через
  dnd-kit pointer events, откат нелегального хода, сброс, навигация) и
  `pgn-upload` (4 кейса: успех, 1 МБ+1, `.txt`, анализ загруженного PGN).
- [x] **Документация и версия.** SPEC §2.6 (семантическая проверка),
  CHANGELOG `[1.1.0]`, версия 1.1.0 в pyproject/app/web, отчёт
  `reports/v1.1.0/ITERATION-37.md`; README/GETTING_STARTED/ARCHITECTURE —
  актуальные счётчики.

### Метрики

- **Backend-тесты:** **919 passed, 3 skipped, 1 xfailed** (79 файлов,
  **249.22 с**, exit 0; +50 к итерации 36). Skip: 2 cairo-кейса +
  `DEEPSEEK_API_KEY`; xfail — KI-012.
- **Frontend-тесты:** **111 passed** (11 файлов; +30).
- **Сборка:** main chunk **112.42 kB gzip**, async `analysisStore`-чанк
  143.35 kB gzip, CSS 4.81 kB gzip, 694 модуля, 7.33 с.
- **E2E mocked:** **42 passed** (21 сценарий × chromium/mobile), 28.4 с.
- **ruff / mypy:** 0 / 0 (86 файлов); **alembic:** схема не менялась.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Геометрия связки, оценки, мотивы и «лучший ход» сверяются с фактами | Семантический слой проверяет только существование фигуры на поле; атрибуция оценок/мотивов — отдельный NLI-слой | KI-023 (частично) |
| Утверждение считается валидным, если верно в любой позиции партии | Привязка утверждения к конкретному полуходу требует разметки предложений по ходам; «любая позиция» выбрана против ложных срабатываний | KI-023 |
| Загрузка PGN: MIME-снифинг | Проверяется расширение (по ТЗ), не содержимое | T-071 |
| Варианты не сохраняются | Вариант — временный просмотр; сохранение/экспорт вариантов не требовался | T-070 |

### Что дальше

- KI-023: атрибуция оценок/мотивов/«лучшего хода» к полуходам (сверка с
  пакетом фактов), привязка утверждений к конкретной позиции.
- T-070: сохранение вариантов, стрелки-подсказки на доске.
- Пост-релизный бэклог: T-111…T-119, KI-030 (качество LLM).

## Итерация 38 — Chat Context & Visual Arrows (T-072, T-073)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Версия — **1.2.0**.

### Сделано

- [x] **T-072, контекст выбранного хода в чате.** `ChatRequest.selected_ply`
  (`int | None`, `ge=0`) проходит в `answer_question` и `stream_chat`;
  `format_selected_ply_context` добавляет в пользовательский промпт строку
  «Пользователь сейчас смотрит на ход 13... Re8 (13-й ход чёрных,
  оценка +0.40 за белых). Если вопрос без явного номера — «этот ход»,
  «почему плохо», «а лучше?» — отвечай применительно к нему.» (RU/EN;
  для 0 — стартовая позиция). Строка входит в `prompt_hash`, поэтому кэш
  не переиспользует ответ для другого полухода; `None`/неотрицательные
  вне анализа/устаревшие значения молча игнорируются. `selected_ply`
  добавлен в SSE `meta`; фронтенд шлёт его только когда `> 0`, над чатом
  — «Контекст: 13... Re8».
- [x] **T-073, стрелки на доске.** `lib/arrows.ts`: `extractSanMoves`
  (семантика backend `SAN_PATTERN`, включая пешечные ходы) и
  `buildArrows` (рекомендация выбранного полухода — зелёная; SAN из
  комментария резолвится на текущей позиции через `chess.js`,
  нелегальные пропускаются; красная — если ход основной линии
  `blunder`/`mistake`/`inaccuracy`, иначе зелёная; дедуп по from/to,
  лимит 3). `Board` принимает `arrows` (react-chessboard 5
  `options.arrows`); `commentaryText` поднят в `analysisStore`
  (не персистится), стрелки скрыты в режиме варианта.
- [x] **Промпты объясняют причины.** Во все уровневые промпты (RU и EN) и
  `chat_en.txt` добавлено правило «ОБЪЯСНЯЙ ПРИЧИНЫ / ALWAYS EXPLAIN WHY»
  с примером «Слабое поле f6 … потому что пешка g7 ушла вперёд»; в
  системный промпт чата — «Всегда объясняй причины».
- [x] **LLM-evals: метрика объяснений.** `has_explanation` (+
  `EXPLANATION_MARKERS`), флаг `has_explanation` в JSON-выгрузке, строка
  «Доля объяснений (потому что/так как/because): X% (N/M)» в сводке и
  консольном итоге, колонка «Объяснения» в per-level таблице.
- [x] **Тесты.** Backend +30: `test_chat_context.py` (9),
  `test_evaluate_llm_explanations.py` (12), расширены `test_api_chat.py`
  (+4: `selected_ply` в /v1/chat и /chat/stream, 422 на отрицательный, без
  поля — без контекста) и `test_prompts.py` (+7: правило во всех 7
  файлах). Frontend +18 (`arrows.test.ts`). E2E +2 сценария:
  `chat-context` (клик по ходу → `selected_ply` в теле запроса → ответ;
  начало партии → поле не отправляется) и `board-arrows` (зелёная стрелка
  рекомендации, стрелки из SSE-комментария, цвет/stroke, маркеры).
- [x] **Версия и документы.** 1.2.0 в pyproject/app/web; CHANGELOG
  `[1.2.0]`, README/GETTING_STARTED/ARCHITECTURE — счётчики и разделы;
  отчёт `reports/v1.2.0/ITERATION-38.md`.

### Метрики

- **Backend-тесты:** **949 passed, 3 skipped, 1 xfailed** (81 файл,
  **247.08 с**, exit 0; +30 к итерации 37). Skip: 2 cairo-кейса +
  `DEEPSEEK_API_KEY`; xfail — KI-012.
- **Frontend-тесты:** **129 passed** (12 файлов; +18).
- **Сборка:** main chunk **112.49 kB gzip**, async workspace-чанк
  143.95 kB gzip, CSS 4.81 kB gzip, 5.79 с.
- **E2E mocked:** **46 passed** (23 сценарий × chromium/mobile), 35.5 с.
- **ruff / mypy:** 0 / 0 (86 файлов); **alembic:** схема не менялась.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Пересид промптов в БД dev-стенда | `seed_prompts` вставляет только отсутствующие строки; в `chessannotator_iter37.prompts` остались старые версии — при живом прогоне evals промпты нужно обновить (админка/пересид) | эксплуатация |
| Стрелки для упоминаний ходов других полуходов | SAN резолвится в текущей позиции (иначе нужна привязка хода к полуходу) | бэклог |
| Стрелки во время SSE-стрима | Текст комментария фиксируется после завершения/правки, чтобы не перерисовывать доску на каждом дельте | бэклог |
| `selected_ply` в чате для 0 | Решено отдавать строку «стартовая позиция» (ТЗ), фронтенд 0 не отправляет | документация |

### Что дальше

- Обновление промптов в БД стенда + живой прогон evals с метрикой
  объяснений.
- Привязка упомянутых ходов к полуходам и стрелки в режиме варианта.
- Пост-релизный бэклог: T-111…T-119, KI-030, KI-023 (атрибуция оценок).

## Итерация 39 — GPU Transfer Prep, PWA & User Profile (T-080, T-081, T-082)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Версия — **1.2.0** (без бампа: продолжение
пост-релизной полировки v1.2.0).

### Сделано

- [x] **T-080, подготовка к переносу обучения на GPU.**
  `.gitignore`: `models/`, `*.safetensors`, `*.pt`, `data/lora_*.jsonl`,
  `data/*.jsonl` (при сохранённом `!data/.gitkeep`) — веса не попадут в
  git. Новый `scripts/generate_lora_dataset_from_db.py` (async, CLI:
  `--limit` 500, `--level`, `--min-words`, `--seed`, `--dry-run`,
  `--output`) собирает датасет schema v2 из БД, переиспользуя хелперы
  `scripts/export_dataset.py`, со сводкой экспорта/пропусков и exit code 2
  на пустых данных. `docs/development/GPU_TRAINING_GUIDE.md`: клонирование,
  venv, PyTorch CUDA 12.1, БД на `POSTGRES_PORT=5434`, генерация датасета,
  `train_lora.py`, VRAM-заметки для 12 ГБ, перенос адаптера
  (`models/lora-club-v1`) архивом или Git LFS, проверка на основном ПК,
  troubleshooting.
- [x] **T-081, PWA.** `vite-plugin-pwa` 2.x: манифест (name/short_name
  «ChessAnnotator», `standalone`, `theme_color`/`background_color` из
  палитры приложения, иконки 192/512 + maskable, apple-touch-icon),
  service worker `generateSW` с precache 15 файлов (937.66 KiB) и
  `navigateFallback: /index.html`; `navigateFallbackDenylist` для
  `/v1/`, `/auth/`, `/admin`, `/health` — API не кэшируется (комментарий в
  конфиге); iOS-мета-теги в `index.html`; иконки сгенерированы Pillow и
  лежат в `web/public/icons/`.
- [x] **T-082, профиль + реальный SMTP.**
  Миграция `a9f1f28d4c45` (`users.display_name`, nullable String(120)),
  `PATCH /auth/me` (имя/уровень, trim, 422 на пустое имя/неизвестный
  уровень, extra="forbid"), `POST /auth/change-password` (Argon2-проверка
  текущего, 401 `invalid_credentials`, 422 на короткий/совпадающий новый).
  Фронтенд: `/profile` (имя, уровень, смена пароля с клиентской
  валидацией, `PATCH /auth/me`, `POST /auth/change-password`, пункт меню
  «Профиль»). SMTP: `SMTPEmailSender` на `aiosmtplib>=3`
  (`SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS/SSL/TIMEOUT`), выбор по
  `EMAIL_SENDER=mock|smtp`, ошибки конфигурации → `EmailDeliveryError`,
  `.env.example` обновлён, `aiosmtplib` в зависимостях.
- [x] **Тесты.** Backend +51 → **1000 passed**: `test_auth_profile.py`
  (PATCH/change-password, валидации, 401/422, логин новым паролем),
  `test_email_sender.py` (SMTP-аргументы через мок `aiosmtplib.send`,
  MIME-заголовки, ошибки, выбор sender, неизменный mock-outbox),
  `test_generate_lora_dataset.py` (формат v2, фильтры/лимит/seed,
  dry-run, exit 2), расширен `test_auth_api.py` (display_name в payload).
  Frontend: без новых unit-файлов; E2E +7 сценариев: `pwa` (манифест,
  иконки, theme-color, регистрация SW) и `profile` (6 кейсов: открытие,
  уровень, имя, несовпадение/короткий пароль без запроса, успешная смена,
  неверный текущий пароль).
- [x] **Документация.** README (PWA/профиль/SMTP/GPU-перенос, версия
  v1.2.0, счётчики), CHANGELOG `[1.2.0]` дополнен, TODO
  T-080/T-081/T-082, отчёт `reports/v1.2.0/ITERATION-39.md`,
  GPU_TRAINING_GUIDE.

### Метрики

- **Backend-тесты:** **1000 passed, 3 skipped, 1 xfailed** (83 файла,
  **295.90 с**, exit 0; +51 к итерации 38). Skip: 2 cairo-кейса +
  `DEEPSEEK_API_KEY`; xfail — KI-012.
- **Frontend-тесты:** **129 passed** (12 файлов; без изменений).
- **Сборка:** main chunk **113.64 kB gzip** (365.55 kB), async
  workspace-чанк 143.95 gzip, CSS 4.81 gzip, 8.07 с; **PWA**: сгенерирован
  `sw.js` + `workbox-*.js`, precache 15 записей (937.66 KiB).
- **E2E mocked:** **60 passed** (30 сценариев × chromium/mobile), 36.6 с.
- **ruff / mypy:** 0 / 0 (86 файлов); **alembic:** новая ревизия
  `a9f1f28d4c45`, `upgrade head` + `check` чисто; `pip check` — без
  конфликтов (aiosmtplib 5.1.3).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Само LoRA-обучение на GPU | Нужна машина с RTX 3060; подготовлены скрипт, гайд и защита весов | T-080 → KI-030 |
| Реальная отправка письма через SMTP | Нет SMTP-провайдера/кредов в этой среде; sender покрыт unit-тестами с моком `aiosmtplib` | T-082 |
| Проверка PWA в Safari/iOS | Нет iOS-устройства; мета-теги и apple-touch-icon добавлены, проверено в Chromium | T-081 |
| Офлайн-режим сверх статики | SW кэширует только статику; офлайн-данные (анализ/чат) осознанно не поддерживаются | T-081 |
| Отзыв сессий при смене пароля | `revoked_before`/`iat_ms` используется в reset-password; для change-password не подключали, чтобы не менять контракт | бэклог |

### Что дальше

- Перенести обучение на RTX 3060 по `GPU_TRAINING_GUIDE.md`, привезти
  адаптер `models/lora-club-v1` и прогнать LLM-evals (метрика объяснений
  уже есть).
- Настроить реальный SMTP-провайдер (`EMAIL_SENDER=smtp`).
- Проверить установку PWA на iOS/Android вручную.
- Пост-релизный бэклог: T-111…T-119, KI-030, KI-023 (атрибуция оценок).

## Итерация 40 — Stripe Integration & Billing (T-083, T-084, T-085)

**Дата:** 2026-10-30
**Статус:** ✅ завершена. Версия — **1.2.0** (бамп не делался; каталог
отчёта `v1.4.0` — по постановке итерации 40).

### Сделано

- [x] **T-083, Stripe Checkout и webhook.** `stripe>=16.1` в зависимостях;
  `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET`/`STRIPE_PRICE_ID` в
  `Settings` (+ `billing_configured`/`billing_webhook_configured`).
  Миграция `f2b3c4d5e6a7`: `users.tier` (`free`/`pro`, NOT NULL,
  server_default `free`, CHECK `ck_users_tier`, backfill admin/auditor →
  pro) и `users.stripe_customer_id` (nullable UNIQUE). Новый пакет
  `app/billing/`: `BillingGateway`/`StripeBilling` (Customer
  find-or-create, Checkout `mode=subscription`, Customer Portal,
  `stripe.Webhook.construct_event`), идемпотентный
  `apply_webhook_event`. Эндпоинты `POST /v1/billing/create-checkout-session`
  (JWT, `{url}`, 409 для уже Pro), `POST /v1/billing/create-portal-session`
  (JWT, 409 без Customer) и `POST /v1/billing/webhook` (без JWT, подпись
  обязательна): `checkout.session.completed` → `tier='pro'` +
  `stripe_customer_id`, `customer.subscription.deleted` → `tier='free'`.
  Без ключей — 503 `billing_not_configured`; success/cancel URL строятся из
  `APP_BASE_URL` (без open redirect).
- [x] **T-084, Billing UI.** Кнопка «Оформить подписку» в модалке
  дневного лимита (POST → `window.location.href`); страницы
  `/billing/success` (подтягивает `tier` через `GET /auth/me`) и
  `/billing/cancel`; блок «Моя подписка» в `/profile` — текущий тариф,
  для Free «Оформить подписку», для Pro «Управлять подпиской» (Stripe
  Customer Portal); `tier` в `UserProfile`/`AuthUser`; i18n RU/EN.
- [x] **T-085, FinOps из БД.** `TokenBudgetMiddleware` и
  `GET /v1/users/me/usage` берут тир из `users.tier` (PK-lookup на
  защищённый запрос; ошибка БД → безопасный `free`), `PRO_IPS` и роль JWT
  больше не дают Pro; `tier` в `/auth/me`. Тестовый seam
  `tier_lookup` — unit-тесты FinOps без PostgreSQL.
- [x] **Тесты.** Backend +29 → **1029 passed**: `test_billing_gateway.py`
  (аргументы SDK, find-or-create Customer, ошибки, HMAC-подпись webhook),
  `test_billing.py` (checkout/customer persistence/reuse, 409/503/401,
  подписанные mock-события меняют `users.tier`, идемпотентность, Portal),
  FinOps DB-tier (`test_token_budget_api.py`, `test_usage_roles.py`,
  `test_token_budget.py`), миграционные колонки (`test_models.py`).
  Frontend +3 → **132 passed** (`api.billing.test.ts`). E2E +4 сценария →
  **68 passed** (`billing.spec.ts`: upgrade из модалки, профиль Free,
  профиль Pro + Portal, страница отмены).
- [x] **Документация.** SPEC §4/§5 (схема, billing-эндпоинты, FinOps),
  ARCHITECTURE §2/§5.5/§6, GETTING_STARTED (счётчики, `STRIPE_*`),
  USER_GUIDE §7.11, E2E README, README (возможности, API, counters),
  CHANGELOG `[Unreleased]`, TODO T-083/T-084/T-085, KNOWN_ISSUES KI-059,
  отчёт `reports/v1.4.0/ITERATION-40.md`.

### Метрики

- **Backend-тесты:** **1029 passed, 3 skipped, 1 xfailed** (85 файлов,
  **132.22 с**, exit 0; +29 к итерации 39). Skip: 2 cairo-кейса +
  `DEEPSEEK_API_KEY`; xfail — KI-012. Stockfish 19 подключён через
  `STOCKFISH_PATH` — engine-тесты не скипались.
- **Frontend-тесты:** **132 passed** (13 файлов; +3).
- **Сборка:** main chunk **115.16 kB gzip** (371.60 kB), async
  workspace-чанк 143.95 kB gzip, CSS 4.81 kB gzip, 3.25 с; PWA precache
  15 записей (943.57 KiB).
- **E2E mocked:** **68 passed** (34 сценария × chromium/mobile), 24.6 с.
- **ruff / mypy:** 0 / 0 (**90** файлов); **pip check:** без конфликтов
  (stripe 16.1.0); **alembic:** ревизия `f2b3c4d5e6a7`, `upgrade head` +
  `check` + roundtrip `downgrade a9f1f28d4c45 → upgrade head` — чисто.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Живой Stripe Checkout/webhook | Нет ключей и тестового Stripe-аккаунта в этой среде; контракт покрыт unit/ integration (мок SDK + реальная HMAC-подпись) и E2E на моках | KI-059 |
| Реальный Customer Portal в браузере | Требует Stripe Customer и ключей; URL/аргументы проверены unit-тестами | KI-059 |
| Гибридные тарифы/лимиты по моделям | В постановке только Free 50k → Pro 500k; доступ к «тяжёлым моделям» остаётся маркетинговым текстом | бэклог |
| Отмена подписки через свой UI | Управление — в Stripe Customer Portal (как в постановке); собственный cancel-эндпоинт не делали | бэклог |
| Бэкфилл `stripe_customer_id` для старых Pro | Старых Pro-подписок нет (billing вводится впервые); admin/auditor получили `tier='pro'` миграцией | — |

### Что дальше

- Создать тестовый Stripe-аккаунт, заполнить ключи, прогнать живой
  Checkout тестовой картой и webhook из Dashboard (KI-059).
- Добавить план/цену в UI (сейчас цена задаётся в Stripe, интерфейс не
  показывает сумму).
- Пост-релизный бэклог: T-111…T-119, KI-030, KI-023, KI-059.

## Итерация 42 — External Dataset Converter (T-087)

**Дата:** 2026-10-31
**Статус:** ⚠️ частично — код и целевые тесты готовы; полная сюита
проверяется на CI (локальное окружение нестандартное — PostgreSQL 5437,
Stockfish нет в PATH).

### Сделано

- [x] **T-087, конвертер внешних PGN-датасетов.**
  `scripts/convert_external_datasets.py`: JSONL с PGN в поле `text` →
  `python-chess` → комментарии `{ ... }` → очистка `[%eval ...]`/`[%clk ...]`
  → склейка → лимит 2000 символов → `build_fact_package(..., level="club")`
  → `schema_version=2`. CLI: `--input`, `--output`, `--limit`, `--dry-run`;
  сводка прочитанных/пропущенных/сконвертированных записей и средней длины
  `assistant`.
- [x] **Пакет фактов без анализа движка.** `build_fact_package` принимает
  `analyses=None` и явно помечает, что оценок/ключевых моментов движка в
  пакете нет (внешний PGN).
- [x] **Тесты.** `tests/unit/test_convert_external_datasets.py` — 37 тестов;
  `tests/unit/test_prompts.py` — +1 тест на пакет без анализа (32 passed в
  файле).
- [x] **Документация и gitignore.** `.gitignore`:
  `data/external_datasets/`, `data/lora_gold_v1.jsonl`;
  `GPU_TRAINING_GUIDE.md` §5.1; TODO T-087; CHANGELOG `[Unreleased]`.

### Метрики

- **Целевые backend-тесты:** `37 passed` (конвертер) + `32 passed`
  (промпты), exit 0.
- **ruff:** `All checks passed!`; **mypy app:** 0 ошибок (90 файлов).
- **Frontend:** `typecheck` exit 0; `132 passed` (13 файлов);
  `build` 7.76 с, main chunk 115.16 kB gzip.
- **Реальный прогон:** `bishop_train.jsonl` → `data/lora_gold_v1.jsonl`
  (5000 записей, 39.2 МБ): прочитано 12 693, битый PGN 7 321, нет
  комментариев 372, средняя длина `assistant` 1 744 символа.
- **Валидация:** `train_lora.py --dry-run` — `schema_version: 2=5000`,
  `club=5000`, ошибок нет.
- **Полная сюита:** локально не завершена (зависает на интеграционных
  тестах без Stockfish/порта БД из CI); запускается на CI.

### Что дальше

- Прогнать полную backend-сюиту на CI и зафиксировать счётчик.
- Обучить LoRA на `data/lora_gold_v1.jsonl` (или смеси с
  `data/lora_club_v1.jsonl`) на GPU-машине.

## Итерация 43 — GGUF Exporter & Training Script Polish (T-088, T-089)

**Дата:** 2026-10-31
**Статус:** ✅ завершена локально (целевые тесты и линтеры зелёные; полная
сюита — на CI, реальное обучение/экспорт — на GPU-машине).

### Сделано

- [x] **T-088, экспорт LoRA → HF → GGUF.** `scripts/export_to_gguf.py`:
  `peft.PeftModel.from_pretrained` → `merge_and_unload()` →
  `save_pretrained` + токенизатор; поиск `llama.cpp`
  (`convert_hf_to_gguf.py`/`convert.py`) и конвертация через subprocess.
  CLI: `--base_model`, `--adapter_path`, `--output_dir`,
  `--llama_cpp_dir`, `--gguf_outtype`, `--device`. Без llama.cpp/при сбое
  конвертации — warning, HF-модель сохраняется, exit 0.
- [x] **T-089, resume и логирование.** `scripts/train_lora.py`:
  `--resume_from_checkpoint PATH` с проверкой каталога и передачей в
  `Trainer.train`; `--report_to auto|tensorboard|wandb|none`
  (`auto` — TensorBoard, иначе wandb, иначе консоль), `logging_dir=
  output_dir/runs`; `--check-env` показывает tensorboard/wandb.
- [x] **Тесты.** `tests/unit/test_export_to_gguf.py` — экспортёр (моки
  peft/transformers/subprocess, graceful fallback); `test_train_lora.py` —
  парсер новых флагов, `resolve_report_to`, `resolve_resume_checkpoint`,
  полный `train()` с fake-стеком и проверкой `Trainer.train`.
- [x] **Документация.** `GPU_TRAINING_GUIDE.md` §10 (Ubuntu 22.04 + CUDA
  12.1, RTX 4090, batch 4/grad 4, resume, экспорт GGUF, LM Studio);
  TODO T-088/T-089; CHANGELOG `[Unreleased]`.

### Метрики

- **Целевые backend-тесты:** `test_export_to_gguf.py` + `test_train_lora.py`
  — **88 passed**; связанный набор (генератор датасета, экспорт, конвертер,
  промпты) — **201 passed**.
- **ruff:** `All checks passed!`; **mypy app:** 0 ошибок (90 файлов);
  **mypy scripts:** 0 ошибок (`export_to_gguf.py`, `train_lora.py`).
- **Smoke:** `train_lora.py --dry-run --dataset data/lora_gold_v1.jsonl
  --limit 3` — `schema_version: 2=3`, `report_to=auto`, `resume=нет`,
  `DRY-RUN OK`; `export_to_gguf.py --help` показывает все флаги.
- **Не выполнено локально:** реальное обучение и merge/GGUF (нет GPU,
  torch/peft/llama.cpp); полная backend-сюита — CI.

### Что дальше

- Обучить адаптер на RTX 4090/3060 по §10, проверить TensorBoard и
  `--resume_from_checkpoint` на реальном чекпоинте.
- Выполнить `export_to_gguf.py`, загрузить GGUF в LM Studio и прогнать
  smoke `/v1/commentary`.

## Итерация 44 — Fixing LoRA Training Pipeline & GPU Guide (T-090, T-091)

**Дата:** 2026-10-31
**Статус:** ✅ завершена локально (целевые тесты и линтеры зелёные;
реальный прогон на A5000 — следующий шаг, полная сюита — CI).

### Сделано

- [x] **T-090, совместимость `train_lora.py` с transformers 4.44+.**
  `collate_batch` возвращает `torch.Tensor` (`input_ids`, `attention_mask`,
  `labels`, `torch.long`) вместо списков; `logging_dir` и `warmup_ratio`
  убраны из `TrainingArguments`; добавлен `--skip-too-long` (по умолчанию
  True), `TooLongError`, счётчик `skipped_too_long` и предупреждения;
  `--no-skip-too-long` возвращает падение. Токенизация идёт до загрузки
  модели, поэтому «все записи длинные» выявляется до траты GPU.
- [x] **T-091, GPU-гайд для РФ.** `hf download` вместо устаревшего
  `huggingface-cli`; `HF_ENDPOINT=https://hf-mirror.com` и
  `snapshot_download`; установка через `uv`; зеркало PyTorch SJTU
  `mirror.sjtu.edu.cn/pytorch-wheels/cu126/`; `torch` + `torchvision`
  одной сборкой `cu126` (проверка обеих версий); §10 переименован под
  CUDA 12.6, добавлены RTX A5000 и траблшутинг.
- [x] **Тесты.** `test_train_lora.py`: коллат возвращает тензоры (мок
  `torch.tensor`), парсер `--skip-too-long`, `train()` пропускает длинную
  запись и падает с `--no-skip-too-long`; проверка отсутствия
  `logging_dir`/`warmup_ratio`.
- [x] **Документация.** `GPU_TRAINING_GUIDE.md` §2/§10, TODO T-090/T-091,
  CHANGELOG `[Unreleased]`.

### Метрики

- **Целевые backend-тесты:** `test_train_lora.py` — **73 passed**;
  `test_export_to_gguf.py` — **18 passed**; связанный набор — **204 passed**.
- **ruff:** `All checks passed!`; **mypy app:** 0 (90 файлов);
  **mypy scripts/tests:** 0 (`train_lora.py`, `export_to_gguf.py`,
  `test_train_lora.py`; добавлен override для torch/transformers/peft/
  datasets, чтобы проверка работала без GPU-пакетов).
- **Smoke:** `collate_batch` на реальном torch — три `Tensor` `int64`
  (input_ids/attention_mask/labels); `--dry-run` печатает
  `skip_too_long=да`, `report_to=auto`, `resume=нет`, `DRY-RUN OK`.
- **Не выполнено локально:** реальное обучение на A5000, полная
  backend-сюита — CI/GPU-машина.

### Что дальше

- Повторить обучение на A5000 (CUDA 12.6, `cu126`) с `--skip-too-long`:
  убедиться, что первая итерация проходит, а чекпоинты пишутся.
- Проверить TensorBoard (по умолчанию `output_dir/runs`) и
  `--resume_from_checkpoint` на реальном чекпоинте.

## Итерация 45 — Lichess Golden Dataset Builder (T-092, T-093)

**Дата:** 2026-11-01
**Статус:** ✅ завершена локально (тесты с моками API/LLM, линтеры и
типы зелёные); реальные Lichess-загрузка и локальная LLM — на машине с
сетью/моделью, полная сюита — CI.

### Сделано

- [x] **T-092, Lichess golden dataset builder.**
  `scripts/fetch_lichess_games.py`: `GET /api/games/user/{username}` или
  `POST /api/games/export/_ids`, `evals=true`, фильтр WhiteElo/BlackElo
  1800–2200, standard-вариант, наличие `[%eval]`, запись
  `data/gold_pgns/*.pgn` по 50–100 партий.
  `scripts/build_gold_dataset.py`: парсинг `[%eval …]` (White POV →
  сторона хода), `MoveAnalysis` с `best_move_known=False`, пакет фактов,
  mock/local LLM (русский ответ), валидация через
  `app/engine/validator.py`, JSONL `schema_version=2`.
  В `MoveAnalysis` добавлен `best_move_known`; `build_fact_package`
  печатает «Лучший ход движка неизвестен» вместо выдуманного хода.
- [x] **T-093, бесплатные GPU.** `GPU_TRAINING_GUIDE.md` §11: Kaggle
  Notebooks (T4, 30 ч/нед, датасет, обучение 4B, TensorBoard, zip) и
  Google Colab (Drive, установка, обучение, resume), ограничения 16 GB.
- [x] **Тесты.** `test_fetch_lichess_games.py` (16) — MockTransport,
  URL/параметры, POST-ID, фильтры, чанки, CLI; `test_build_gold_dataset.py`
  (27) — `[%eval]`/мат, `MoveAnalysis`, v2-контракт, skip no-evals/
  invalid-pgn/invalid-llm, retry, mock/local, CLI; `test_prompts.py` +1 —
  пакет без лучшего хода.
- [x] **Документация.** TODO T-092/T-093, CHANGELOG `[Unreleased]`,
  `.gitignore` для `data/gold_pgns/` и `data/gold_dataset.jsonl`.

### Метрики

- **Целевые backend-тесты:** fetch **16 passed**, build **27 passed**,
  prompts **33 passed**; связанный набор — **248 passed** (30.15 с).
- **ruff:** `All checks passed!`; **mypy app:** 0 (90 файлов);
  **mypy новых скриптов/тестов:** 0 (4 файла).
- **Smoke:** `build_gold_dataset.py --provider mock` на PGN с
  `[%eval 0.20 … -1.50]` → 1 запись, `schema_version=2`, в пакете
  «Потеря относительно лучшей линии: 160 cp» и «Лучший ход движка
  неизвестен», assistant 291 символ.
- **Не выполнено локально:** реальный вызов Lichess API и локальной LLM
  (в тестах — моки); полная backend-сюита — CI.

### Что дальше

- Скачать 1000+ PGN Lichess с `evals=true` (рейтинг 1800–2200) и собрать
  `data/gold_dataset.jsonl` с локальной LLM.
- Обучить LoRA на золотом датасете на A5000 или бесплатном T4 (§10–§11).

## Итерация 46 — Prompt Terminology & Kaggle Notebook (T-094)

**Дата:** 2026-11-01
**Статус:** ✅ завершена локально (промпт, ноутбук, тесты, ruff и mypy
зелёные); реальный прогон на Kaggle/T4 и полная backend-сюита — CI/GPU.

### Сделано

- [x] **T-094, терминология «ход».** `app/llm/prompts.py`: метаданные —
  `Ходов: N` без счётчика полуходов, ключевой момент — `Ход N
  белых/чёрных` (номер хода, не ply). `app/rag/context.py` выводит
  `Ход N белых/чёрных`; `scripts/build_gold_dataset.py` синхронизирован;
  в пакет фактов попадает номер хода.
- [x] **T-094, Kaggle Notebook.** `notebooks/kaggle_train_lora.ipynb`:
  установка зависимостей, клонирование репозитория, автопоиск
  `gold_dataset.jsonl`, `--dry-run`, QLoRA Qwen2.5-7B (`batch=1`,
  `grad_accum=16`, `max_length=2048`, `--load-in-4bit`), TensorBoard,
  zip-адаптер и resume. `GPU_TRAINING_GUIDE.md` §11.1 — быстрый старт за
  5 минут со ссылкой на ноутбук.
- [x] **T4 dtype.** `scripts/train_lora.py` выбирает fp16, если bf16
  недоступен (T4/P100), и передаёт fp16 в `TrainingArguments`; иначе
  QLoRA на T4 могла идти в bf16/без mixed precision и падать.
- [x] **Тесты.** `test_prompts.py` — нет слова «полуход» в пакете и
  системном промпте, номер хода для чёрных; `test_train_lora.py` — fp16
  fallback; `test_kaggle_notebook.py` — 5 проверок контракта ноутбука;
  фикстура `lora_sample_v2.jsonl` приведена к новому формату.
- [x] **Документация.** TODO T-092 (прогресс 130 записей), T-094;
  CHANGELOG `[Unreleased]`; RELEASES (эта запись).

### Метрики

- **Целевые backend-тесты:** prompts + build_gold + kaggle — **70
  passed**; связанный набор (prompts, build_gold, kaggle, train_lora,
  dataset_export, generate_lora_dataset, convert_external,
  export_to_gguf) — **244 passed** (3.46 с).
- **ruff:** `All checks passed!`; **mypy app:** 0 (90 файлов);
  **mypy app + изменённые скрипты/тест ноутбука:** 0 (93 файла).
- **Smoke:** `build_gold_dataset.py --provider mock` → 1 запись,
  `Ходов: 2`, `## 1. Ход 2 белых: Nf3`, слова «полуход» нет.
  `train_lora.py --dry-run --dataset data/gold_dataset.jsonl` → 19
  записей, `schema_version=2`, `DRY-RUN OK`.
- **Не выполнено локально:** реальный Kaggle/T4 (7B/2048), скачивание
  Qwen2.5-7B, полная backend-сюита — окружение/CI.

### Что дальше

- Импортировать ноутбук в Kaggle, загрузить 130 записей как Dataset
  `chess-gold` и прогнать T4; при OOM — 4B/1024.
- Пересобрать/нормализовать старые 130 записей: они созданы до фикса
  промпта и могут содержать «полуход» в user/assistant.

---

## Итерация 47 — Colab/T4 Compatibility & Dataset Truncation (T-095)

**Дата:** 2026-11-02
**Статус:** ✅ завершена локально (код, ноутбук, гайд, тесты, ruff и mypy
зелёные); реальный прогон на Colab/T4 и полная backend-сюита — CI/GPU.

### Сделано

- [x] **T-095, лимит пакета фактов.** `build_fact_package(...,
  max_tokens=2048)`: длинная партия сокращается до первых/последних 10
  ходов и ходов ключевых моментов, в пакет добавляется пометка о
  сокращении; ключевые моменты сохраняются, пока хватает лимита.
  `max_tokens < 1` → `ValueError`.
- [x] **T-095, TensorFlow в Colab.** `scripts/train_lora.py` на импорте
  выставляет `USE_TF=0` и `TRANSFORMERS_NO_TF=1` до ленивого импорта
  torch/transformers: TF больше не инициализирует CUDA 13.0 и не уводит
  PyTorch на CPU.
- [x] **T-095, `--force-fp16` и T4.** Новый CLI-флаг принудительно ставит
  `fp16=True`/`bf16=False`; `_looks_like_t4` определяет «Tesla T4» по
  имени устройства и делает то же автоматически, даже если
  `is_bf16_supported()` вернул `True`. `--check-env` предупреждает о T4.
- [x] **T-095, ноутбук.** `torchao>=0.16.0 --no-deps`, отдельная ячейка
  блокировки TF до импорта torch, запуск с `--no-4bit --force-fp16
  --max-length 2048`; модель по умолчанию 4B (7B без 4-bit не влезает в
  T4 16 GB) — 7B оставлена комментарием с `--load-in-4bit`/1024.
- [x] **T-095, гайд.** `GPU_TRAINING_GUIDE.md` §11.1/§11.3 обновлены,
  добавлен §11.3.1 «Решение проблем (Troubleshooting)» (libcudart/TF,
  torchao, bf16/`--force-fp16`, почему `--no-4bit`, OOM) и заметка, что
  `--max-length` считает всю запись.
- [x] **Тесты и документация.** 13 новых unit-тестов (6 prompts, 1
  notebook, 6 train_lora); TODO T-095, CHANGELOG `[Unreleased]`,
  RELEASES (эта запись), отчёт `ITERATION-47.md`.

### Метрики

- **Целевые тесты:** prompts + train_lora + kaggle + build_gold +
  dataset_export + generate_lora + convert_external — **239 passed**
  (9.38 с).
- **Полная unit-сюита:** `pytest tests/unit -q` — **844 passed,
  1 skipped, 1 xfailed** (24.96 с).
- **ruff:** `All checks passed!`; **mypy app:** 0 (90 файлов);
  **mypy app + скрипты + изменённые тесты:** 0 (95 файлов).
- **Smoke обрезки:** партия 1200 ходов, полный пакет — 2141 токен,
  после обрезки — 274 токена при лимите 2048; начало `1. Nf3 Nf6 …`,
  конец `600. Ng1 Ng8`, 3/3 ключевых момента на месте.
- **Smoke dry-run:** `data/gold_dataset.jsonl` — 1000 записей,
  `schema_version: 2=1000`, оценка max ~1810 токенов, `DRY-RUN OK`.
- **TF-блокировка:** `import scripts.train_lora` → `USE_TF=0`,
  `TRANSFORMERS_NO_TF=1`.
- **Не выполнено локально:** реальный Colab/T4 (4B/2048, `--no-4bit`),
  полная backend-сюита с PostgreSQL/Stockfish — окружение/CI.

### Что дальше

- Прогнать ноутбук в Colab: убедиться, что `skipped_too_long = 0`;
  если пропуски остались — это бюджет system+assistant, поднять
  `--max-length` до 3072 (§11.3.1).
- Проверить на T4, что `--no-4bit` + 4B + 2048 помещается в 16 GB; иначе
  `--max-length 1536`.
- Пересобрать golden dataset с новым `build_fact_package`: длинные
  партии теперь дают пакеты ≤ 2048 оценённых токенов.

---

## Итерация 48 — LLM Evaluation & KI-030 Closure (T-096)

**Дата:** 2026-11-02
**Статус:** ✅ завершена — оценка 3B/7B зафиксирована, промпты очищены,
`LLM_TEMPERATURE` покрыт тестом, KI-030 закрыт, KI-060 заведён; тесты,
ruff и mypy зелёные.

### Что сделано

- [x] **T-096, оценка LoRA 3B/7B.** `chess-annotator-3b` (Qwen2.5-3B) —
  **2.87/10** (2.20/2.80/3.60), `qwen-7b-tokenizer` (Qwen2.5-7B) —
  **3.33/10** (2.80/3.40/3.80) против baseline zero-shot Qwen3-4B —
  **4.40/10**. Оба прогона: 5/5 генераций, 0 ошибок `502`, ≈ 10 с.
  Отчёт — `docs/development/reports/LLM_EVAL.md`.
- [x] **`LLM_TEMPERATURE`.** Проверен проброс `Settings.llm_temperature` →
  `app/llm/factory.py` → `OpenAICompatibleClient` → JSON payload
  (`complete` и `stream_generate`) для LM Studio и DeepSeek. Проброс уже
  был корректным; добавлены 4 теста (env → Settings, фабрика → payload
  для club/gm, стрим), `LLM_TEMPERATURE=0.2` — в `.env.example`.
- [x] **Few-shot contamination.** Из `app/prompts/club.txt` и
  `club_en.txt` убраны примеры с конкретными ходами/фигурами; добавлено
  правило «если в пакете нет информации о ходе — не выдумывать ходы и не
  упоминать фигуры, писать только общие слова о позиции».
  `NO_MOVE_DATA_INSTRUCTION` добавлена в пакет без анализа и в обычный
  пакет; 12 новых тестовых кейсов в `test_prompts.py`.
- [x] **KI-030 закрыт.** LoRA на 3B и 7B не дала прироста (2.87 и 3.33
  против 4.40); выявлено переобучение, галлюцинации, путаница сторон и
  языковой дрейф 7B. Возврат к базовой 4B; пересборка golden dataset
  более сильной моделью (DeepSeek) — T-104b.
- [x] **KI-060 заведён.** Языковой дрейф (китайский текст) LoRA 7B:
  причина — переобучение на узком шумном датасете; план — CJK-детектор,
  пересборка данных, переобучение с language-replay.
- [x] **Документация.** `LLM_EVAL.md` (3 модели, дельты, пер-партийные
  оценки, вывод), `KNOWN_ISSUES.md` (KI-030 Closed, KI-060 Open, счётчики
  60/26/34), `CHANGELOG.md`, `README.md`, `ARCHITECTURE.md`,
  `GETTING_STARTED.md`, `LORA_TRAINING.md`, `TODO.md` (T-096, T-104b).
- [x] **Изоляция export-тестов.** Пять тестов
  `test_admin_exports.py`/`test_export_jobs.py` ограничены датами
  сидируемых правок: реальная `gm`-правка от 2026-10-28 в dev-БД больше
  не даёт ложные «2 строки вместо 1».

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Контрольный eval 3B/7B на очищенном промпте | Оценка уже показала разрыв −1.07/−1.53; повторный прогон 5 партий не меняет решение | §5 отчёта ITERATION-48 |
| Пересборка golden dataset (DeepSeek) и переобучение 7B | Требует внешней модели/GPU и нового цикла генерации | T-104b, KI-060 |
| CJK-детектор в eval/валидаторе | Языковой дрейф пока ловится только судьёй | KI-060 |
| Raw JSON 3B | Перезаписан прогоном 7B (общий `llm_eval_results.json`) | LLM_EVAL.md, ограничения |

### Метрики

- **Backend-тесты:** 1196 passed, 9 skipped, 1 xfailed (полная сюита).
- **Целевые тесты:** `test_prompts.py` — 56 passed; `test_llm.py` — 26
  passed; `test_evaluate_llm_explanations.py` — 13 passed.
- **ruff:** `All checks passed!`; **mypy app:** 0 (90 файлов); **mypy app +
  `scripts/evaluate_llm.py` + unit-тесты:** 0 (93 файла).
- **Eval:** 3B — 2.87/10, 7B — 3.33/10, baseline 4B — 4.40/10; 5/5 и 0
  `502` у обеих LoRA-моделей.

### Что дальше

- **T-104b:** пересобрать golden dataset с DeepSeek (меньше шума и
  галлюцинаций), переобучить 7B с меньшим LR/эпохами и language-replay,
  прогнать тот же eval-набор; ориентир — не ниже 4.40/10.
- **KI-060:** CJK/кириллица-детектор в eval и валидаторе, закрыть после
  5/5 чистых русских комментариев у 7B.
- **KI-023:** расширить eval до 30–50 партий и проверять атрибуцию
  оценок/мотивов/«лучшего хода».

---

## Итерация 49 — Data Generation & CJK Detector (T-104b, KI-060, v0.2.0)

**Дата:** 2026-11-03
**Статус:** ✅ завершена (подготовка данных и кода; реальная генерация
v2-датасета и обучение не запускались)

### Что сделано

- [x] **CJK-детектор в `app/commentary/semantic.py`.** `has_cjk`,
  `count_cjk`, `cjk_share`, `strip_cjk_sentences`, `sanitize_cjk`,
  `cjk_warning`; диапазон U+4E00–U+9FFF, порог `CJK_MAX_SHARE = 0.10`.
  `app/commentary/service.py` делает один retry «Отвечай только на
  русском языке. Убери китайские иероглифы», затем вырезает предложения
  с иероглифами или отклоняет ответ; логи — только счётчики.
- [x] **`scripts/build_gold_dataset_v2.py`.** Учитель DeepSeek
  (`deepseek-chat`, ключ `Settings.deepseek_api_key`), два режима:
  PGN из `data/gold_pgns/` (полный пакет фактов, `max_tokens=2048`) и
  `--positions data-chess/positions.jsonl` (упрощённый пакет — только
  FEN, сторона хода, оценка, лучший ход; `best_move` SAN или UCI).
  SAN- и CJK-валидация, retry, `schema_version=2`, `--dry-run` без API.
- [x] **Гиперпараметры v0.2.0.** В `scripts/train_lora.py` выделены
  `DEFAULT_EPOCHS`/`DEFAULT_LR`, help для `--epochs`/`--lr`; значения
  переопределяются флагами (`--lr 1e-4 --epochs 1`). В
  `GPU_TRAINING_GUIDE.md` добавлен §6.1 «Обучение v0.2.0 (DeepSeek
  Teacher)» с командами сборки PGN/positions-датасета и обучения.
- [x] **Документация и KI.** KI-060 переведён в «Open (частично
  исправлен)» с описанием детектора, v2-режимов и остатка (SSE/eval);
  `CHANGELOG.md`, `TODO.md` (T-104b), этот раздел.
- [x] **Тесты.** 70 новых unit-тестов: детектор (включая граничные
  кодовые точки), сервисный retry/вырезание/отказ, v2-билдер с
  `httpx.MockTransport` (PGN + positions), переопределение LR/эпох.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Реальная генерация `data/gold_dataset_v2.jsonl` через DeepSeek | DoD требует покрытия MockTransport; платный API и лимиты не тратились, `--dry-run` проверен на реальных PGN | T-104b |
| Переобучение 7B (1 эпоха, LR 1e-4) и repeat-eval | В итерации 49 нет GPU и явного запрета запускать обучение | T-104b, KI-060 |
| `data-chess/positions.jsonl` | Файл готовит сосед (`generate_positions.py`); режим проверен на фикстурах и smoke-файле | `--positions` |
| CJK в SSE-стриминге | Текст уже отправлен клиенту; нужен отдельный буферизующий фильтр | KI-060 |
| CJK-метрика в `evaluate_llm.py` | Вне DoD итерации; оценка опирается на судью | KI-060 |

### Метрики

- **Backend-тесты:** 1267 passed, 9 skipped, 1 xfailed (полная сюита).
- **Новые тесты:** 70 passed (`test_cjk_detector.py`,
  `test_commentary_cjk.py`, `test_build_gold_dataset_v2.py`).
- **ruff:** `All checks passed!`; **mypy app + scripts:** 0 ошибок.
- **Smoke:** `--dry-run` на реальных PGN — 23 файла, 2–3 пакета; positions
  smoke на схеме `generate_positions.py` — 2 позиции, 2 пакета, 0 API.

### Что дальше

- **T-104b:** запустить `build_gold_dataset_v2.py` на выбранном объёме
  (PGN и/или positions), проверить `train_lora.py --dry-run`, обучить 7B
  с `--lr 1e-4 --epochs 1`, прогнать тот же eval; цель — не ниже
  4.40/10 и 5/5 без CJK.
- **KI-060:** после переобучения закрыть; при необходимости добавить
  CJK-метрику в eval и фильтр для SSE.
- **KI-023:** расширить eval до 30–50 партий.

---

## Итерация 50 — Dataset Ready & Eval Prep (T-104b, KI-060)

**Дата:** 2026-11-03
**Статус:** ✅ завершена (подготовка к полной генерации и обучению 7B на
Kaggle; сам прогон обучения запускается после коммита)

### Что сделано

- [x] **Тестовый прогон `build_gold_dataset_v2.py`.** 10 позиций из
  `positions.jsonl`, DeepSeek Teacher: качество комментариев — идеальное,
  **≈ $0.01**, **0 CJK**-иероглифов. Это подтверждает пайплайн v2 перед
  полной генерацией.
- [x] **CJK-метрика в `scripts/evaluate_llm.py`.** Считается доля
  иероглифов U+4E00–U+9FFF среди непустых символов и число комментариев
  с CJK; выводится в консоль, в `LLM_EVAL.md` (строка итога, колонки
  «CJK» по уровням и по партиям) и в `llm_eval_results.json`
  (`cjk_chars`, `cjk_share`). Скрипт модельно-независим: `qwen-7b-tokenizer`
  оценивается теми же командами через `VLLM_MODEL`/`LORA_ADAPTER_PATH`.
- [x] **Kaggle-раздел `GPU_TRAINING_GUIDE.md`.** Для v0.2.0 указан
  `gold_dataset_v2.jsonl`, `Qwen/Qwen2.5-7B-Instruct`, `--load-in-4bit`,
  `--lr 1e-4 --epochs 1`; §11.1/§11.2 обновлены, ноутбук переведён на
  v2-датасет и `LR=1e-4`/`EPOCHS=1` (4B-дефолт для T4 сохранён).
- [x] **Документация и KI.** `LLM_EVAL.md` (методика + CJK-контроль),
  `CHANGELOG.md`, `KNOWN_ISSUES.md` (пункт про eval-метрику), этот раздел.
- [x] **Тесты.** 12 новых unit-тестов CJK-метрики (подсчёт, сводка,
  отчёт, JSON, имя v2-модели).

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Полная генерация `gold_dataset_v2.jsonl` | Запускается после коммита; тестовый прогон 10 позиций уже подтвердил пайплайн | T-104b |
| Обучение 7B на Kaggle и eval | Следующий шаг после коммита; параметры зафиксированы в гайде | T-104b, KI-060 |
| Живой прогон `evaluate_llm.py` на v2-модели | Нужны запущенная vLLM/LM Studio и `qwen-7b-tokenizer`; код модельно-независим и покрыт тестами | KI-060 |

### Метрики

- **Backend-тесты:** 1271 passed, 17 skipped, 1 xfailed (полная сюита;
  8 live-тестов LM Studio пропущены — сервис недоступен в этой среде).
- **Новые тесты:** 12 passed (`test_evaluate_llm_cjk.py`).
- **ruff:** `All checks passed!`; **mypy app + scripts:** 0 ошибок.
- **Тестовый прогон v2:** 10 позиций, ≈ $0.01, 0 CJK.

### Что дальше

- Закоммитить итерацию 50, запустить полную генерацию v2-датасета и
  обучение 7B на Kaggle (`--lr 1e-4 --epochs 1`), затем прогнать
  `evaluate_llm.py` — CJK-метрика должна дать **0% / 0 комментариев**.
- **KI-060:** закрыть после 5/5 чистых русских комментариев у 7B и
  достижения baseline 4B (4.40/10).

---

## Итерация 52 — PGN Dataset Validation (T-104b, KI-060, KI-061)

**Дата:** 2026-11-03
**Статус:** ✅ завершена (документация; генерация 500+ и обучение v4 не
запускались — нужны DeepSeek API и GPU)

### Что сделано

- [x] **Валидация v3-PGN.** `qwen-7b-tokenizer_gguf-v3`, обученный на
  **50 PGN-партиях** от DeepSeek (`data/gold_dataset_v2_games.jsonl`):
  средняя **3.67/10** (факт. точность 3.00, полезность 4.00, стиль 4.00),
  объяснения **67% (2/3)**, CJK **0.00% (0/3)**. Сравнение с v1 (3.33),
  v2-FEN (2.67) и baseline 4B (4.40) — `LLM_EVAL.md`; сырые данные —
  `llm_eval_results.json`. Из 5 партий оценена 1: 2 упали на Stockfish
  (`Engine unavailable`), 2 ответа судьи без JSON-объекта. Набор партий v3
  отличается от прогонов v1/v2/baseline, поэтому дельты — межверсионные
  (см. `LLM_EVAL.md` «Оговорка о выборке»).
- [x] **KI-061 (Format mismatch) закрыт.** Причина падения v2-FEN —
  несовпадение формата обучения (упрощённый FEN-пакет `--positions`) и
  инференса (полный PGN-пакет); v3 учился на PGN-формате, галлюцинации
  снизились, **+1.00** к v2-FEN. Запись отсутствовала в реестре и
  восстановлена сразу как Closed (итерация 52).
- [x] **KI-060 остаётся Open.** На v3 — впервые **0.00% CJK** (0/3), но
  выборка мала (оценён 1 комментарий), причина (объём данных) не
  устранена, SSE-фильтр не реализован. Счётчики KI: **61 (26 Open,
  35 Closed)**.
- [x] **GPU_TRAINING_GUIDE.md §6.2.** Зафиксировано: для финального
  качества 7B нужно **≥500 PGN-записей** (целевой диапазон 1000–2000);
  сводная таблица прогонов v1/v2-FEN/v3/baseline, команды масштабирования,
  стоимость, требования к v4; обновлены §6.1, §11 и чек-лист.
- [x] **Документация итерации.** `LLM_EVAL.md` (v3 + сравнение),
  `KNOWN_ISSUES.md` (KI-061 Closed, KI-060 с 0% на v3, история),
  `GPU_TRAINING_GUIDE.md` (§6.2), `RELEASES.md`, `CHANGELOG.md`,
  `TODO.md`, отчёт `ITERATION-52.md`.

### Что не сделано и почему

| Не сделано | Причина | Где |
| :--- | :--- | :--- |
| Генерация 500+ PGN и обучение v4 | Нужны DeepSeek API и GPU; итерация документационная — сначала зафиксировать вывод v3 | T-104b, KI-060 |
| Повторный прогон 5/5 на v3 | В текущем прогоне 4/5 ошибок окружения/судьи; без стабилизации Stockfish и судьи повтор не добавит данных | `LLM_EVAL.md` §Ограничения |
| Eval на 30–50 партиях | Следующий шаг v4; на 5 партиях шум судьи сопоставим с разницей между версиями | T-104b, KI-023 |
| Запись об итерации 51 | В `RELEASES.md` нет отчёта; результаты v2-FEN закоммичены в `4197448` после итерации 50, артефакты v2-обучения не сохранены; восстанавливать задним числом — фабрикация | `LLM_EVAL.md` §Ограничения, KI-061 |
| Фиксированный eval-набор | Партии берутся из RAG и меняются между итерациями — межверсионные дельты не same-sample | T-024, KI-023 |
| Историческая воспроизводимость v1/v2 и датасета | Наборы v1/v2 (`game_id` 252/262/267/271/272) в текущей БД отсутствуют; v3-набор воспроизводим (`annotated_games`, те же 5 партий). `data/gold_pgns/` и `data/*.jsonl` gitignored/локальные — пересборка датасета требует исходников и DeepSeek | `LLM_EVAL.md` §Ограничения |

### Метрики

- **Тип итерации:** docs-only (код и тесты не менялись).
- **Eval v3-PGN:** 3.67/10; объяснения 67% (2/3); CJK 0.00% (0/3);
  оценён 1 из 5 комментариев.
- **Проверки:** `pytest -q` — **1281 passed, 7 skipped, 1 xfailed**
  (205.71 с, exit 0; skip — live-тесты с PostgreSQL/Redis, xfailed —
  маркер KI-012); `ruff check .` — `All checks passed!`; `mypy app` —
  0 ошибок (90 файлов); smoke-проверка чисел и счётчиков — exit 0;
  `git diff --check` — exit 0.

### План масштабирования (v4)

1. **Данные:** собрать **500–1000 PGN-записей** PGN-режимом
   `build_gold_dataset_v2.py` (DeepSeek); FEN-режим — только диагностика
   (KI-061).
2. **Обучение:** Qwen2.5-7B QLoRA, 1 эпоха, LR `1e-4`, `max-length` 1024
   (T4) / 2048 (24 GB), `--load-in-4bit`.
3. **Eval:** расширить с 5 до **30–50 партий**; критерии — сначала
   ≥4.40 (baseline 4B), затем ≥7.0 (порог KI-030).
4. **KI-060:** закрыть при 5/5 комментариев без CJK и уровне ≥4.40.
5. **Следующая итерация:** сгенерировать датасет, обучить v4 на Kaggle,
   прогнать расширенный eval; результат — в `LLM_EVAL.md`.

### Что дальше

- Сгенерировать 500+ PGN-записей и обучить v4 (T-104b), затем сравнить с
  v3/baseline на 30–50 партиях.
- Перед v4 зафиксировать eval-набор (30–50 партий, один и тот же для всех
  версий) — T-024, KI-023.
- Закрыть KI-060 после чистого прогона v4; при возврате FEN-режима в
  обучение переоткрыть KI-061.
