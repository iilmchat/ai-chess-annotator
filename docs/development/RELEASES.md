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
