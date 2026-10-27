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
