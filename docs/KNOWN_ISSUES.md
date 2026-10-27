# Known Issues — ChessAnnotator

Реестр известных проблем, ограничений и отклонений окружения.
Формат записи (`RULES.md` §9.2):

```
## KI-NNN · Open|Closed · Low|Med|High|Critical · <модули>
**Симптом.** …
**Причина.** …
**Обход / текущее поведение.** …
**План закрытия.** …
```

Правило: любое отклонение — здесь. «Замазать» молча (широкий `except`,
`--passWithNoTests`, отключённый линтер) запрещено: запись в реестр
обязательна.

## Сводка

| KI | Статус | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- | :--- |
| KI-001 | Open | Low | infra, dev-env | Хостовый порт Redis `6380` занят чужим контейнером → локально `6381` |
| KI-002 | Open | Low | api, dev-env | Хостовый порт `8000` занят чужим контейнером → локально `8010` |
| KI-003 | Open | Low | db, migrations | `downgrade` не удаляет расширение `vector` (осознанно) |
| KI-004 | Open | Low | api, typing | `redis.asyncio.from_url` без аннотаций → узкий `type: ignore` |
| KI-005 | Open | Low | tests | `StarletteDeprecationWarning` про `httpx`/`TestClient` |
| KI-006 | Open | Low | engine, env | Бинарник Stockfish вне репозитория; Windows — вручную |
| KI-007 | Open | Low | docs | README заявляет Vite 8 / Vitest 4, фактически 6.4.5 / 2.1.9 |
| KI-008 | Open | Med | ci, web | CI-шаг `pnpm test` — no-op (`--passWithNoTests`), 0 фронтенд-тестов |
| KI-009 | Open | Low | docs | README ссылается на ещё не созданные документы |

Всего: **9** (Open: 9, Closed: 0). Заведён в итерации 1 (T-006).

---

## KI-001 · Open · Low · infra, dev-env

**Симптом.** `docker compose up -d postgres redis` падает:

```
Error response from daemon: ports are not available: exposing port TCP
127.0.0.1:6380 -> 127.0.0.1:0: listen tcp4 127.0.0.1:6380: bind: Only one
usage of each socket address (protocol/network address/port) is normally permitted.
```

**Причина.** На dev-машине `127.0.0.1:6380` уже опубликован контейнером
`ticket-redis` другого проекта (`0.0.0.0:6380->6379/tcp`).

**Обход / текущее поведение.** Порт Redis в `docker-compose.yml` —
`"127.0.0.1:${REDIS_PORT:-6380}:6379"`. Канон 6380 сохранён (CI, прод,
чистые машины), локально в `.env` задано `REDIS_PORT=6381` и
`REDIS_URL=redis://127.0.0.1:6381/0`. `docker compose ps` показывает
`6379/tcp` → `127.0.0.1:6381`, healthcheck `redis: ok`.

**План закрытия.** Не требует кода. При освобождении 6380 — убрать
`REDIS_PORT` из локального `.env`. В прод-стеке порт наружу не
публикуется вовсе.

---

## KI-002 · Open · Low · api, dev-env

**Симптом.** `uvicorn app.main:app --port 8000` не стартует: порт занят.

**Причина.** `127.0.0.1:8000` занят контейнером `ai-opencamera-api-1`
(другой проект, публикация через Docker Desktop).

**Обход / текущее поведение.** Живая проверка healthcheck выполнена на
`127.0.0.1:8010` (отчёт `reports/iteration-1/STEP-06_HEALTHCHECK.md`).
Ответ API от номера порта не зависит; Vite-прокси в `web/vite.config.ts`
по-прежнему указывает на `localhost:8000` — на этой машине для
фронтенд-разработки нужно либо освободить 8000, либо поправить прокси
локально.

**План закрытия.** Освободить 8000 (остановить чужой контейнер) либо
сделать порт API настраиваемым (`API_PORT` + переменная прокси в Vite) —
кандидат в T-019.

---

## KI-003 · Open · Low · db, migrations

**Симптом.** После `alembic downgrade base` расширение `vector` остаётся
установленным в БД.

**Причина.** `downgrade` миграции `b802598c3778` намеренно не делает
`DROP EXTENSION vector`: расширение — свойство базы, а не схемы; в
общей БД им могут пользоваться другие схемы, а `DROP EXTENSION ... CASCADE`
удалил бы чужие объекты.

**Обход / текущее поведение.** `downgrade base` возвращает только схему
`public` к состоянию «нет таблиц» (проверено), тип `level` удаляется.
Для полностью чистой БД — `docker compose down -v`.

**План закрытия.** Не планируется; при необходимости — отдельная
административная команда вне Alembic.

---

## KI-004 · Open · Low · api, typing

**Симптом.** `mypy app`:

```
app/api/v1/health.py: error: Call to untyped function "from_url" in typed context
```

**Причина.** `redis-py` 5.3 поставляет `py.typed`, но `Redis.from_url`
в inline-типах остаётся без аннотаций (upstream-пробел). Стабы
`types-redis` 4.6 не помогают: они описывают redis 4.x и не знают
`aclose`.

**Обход / текущее поведение.** В `app/api/v1/health.py` узкий
`# type: ignore[no-untyped-call]` с комментарием; `types-redis` удалён из
dev-зависимостей.

**План закрытия.** Убрать ignore, когда redis-py аннотирует `from_url`
(проверять при обновлении `redis`).

---

## KI-005 · Open · Low · tests

**Симптом.** `pytest -q` печатает:

```
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is
deprecated; install `httpx2` instead.
```

**Причина.** Starlette 1.x предупреждает о смене HTTP-клиента в
`TestClient`; текущий `httpx` 0.28 ещё поддерживается.

**Обход / текущее поведение.** На результат не влияет: тесты проходят,
warning показывается в summary.

**План закрытия.** Перейти на `httpx2`/обновить Starlette при следующем
плановом обновлении зависимостей (не раньше Фазы 1).

---

## KI-006 · Open · Low · engine, env

**Симптом.** На чистой машине `GET /v1/health` → `stockfish: {"status":
"error", "detail": "stockfish binary not found"}`.

**Причина.** Бинарник Stockfish (79 МБ) не коммитится и не ставится
автоматически на Windows.

**Обход / текущее поведение.** В CI — `apt-get install -y stockfish`.
Локально (Windows, итерация 1): Stockfish 17.1 распакован в
`D:\Tools\stockfish\stockfish.exe`, в `.env` —
`STOCKFISH_PATH=D:\Tools\stockfish\stockfish.exe`. Healthcheck `ok`.

**План закрытия.** Скрипт `scripts/install-stockfish.ps1` (Windows) +
раздел в `GETTING_STARTED.md`; кандидат в T-008 (Engine Service).

---

## KI-007 · Open · Low · docs

**Симптом.** `README.md` в таблице стека указывает «Vite 8», «Vitest 4»,
«Tailwind CSS 4», «@xyflow/react 12», «Zustand 5», «react-i18next», а
`web/package.json` содержит только `react 19`, `vite ^6.0.5`,
`vitest ^2.1.8` (фактически установлены 6.4.5 и 2.1.9).

**Причина.** README описывает целевой стек, а bootstrap-пакет фронтенда
содержит минимальный набор; остальные библиотеки появятся в T-019…T-022.

**Обход / текущее поведение.** Сборка и typecheck используют фактический
набор; расхождение зафиксировано, в отчётах указываются реальные версии.

**План закрытия.** Либо привести README к факту (T-019), либо добавить
зависимости при реализации UI (T-019/T-020); после этого KI закрывается.

---

## KI-008 · Open · Med · ci, web

**Симптом.** CI-шаг `pnpm test -- --passWithNoTests` всегда зелёный, даже
если фронтенд-тестов нет; `pnpm --dir web test` локально завершается
`exit=1` («No test files found»).

**Причина.** Фронтенд-тестов пока нет: UI-компонентов нет (T-019 —
только скелет). Шаг в CI добавлен авансом.

**Обход / текущее поведение.** `--passWithNoTests` в CI; в README и
отчётах указывается **0** фронтенд-тестов, чтобы зелёный CI не создавал
ложного впечатления покрытия.

**План закрытия.** Первый компонентный тест (Vitest + Testing Library) в
T-019/T-020, затем убрать `--passWithNoTests` из CI.

---

## KI-009 · Open · Low · docs

**Симптом.** `README.md` ссылается на документы, которых ещё нет:
`docs/user_guide/USER_GUIDE.md`, `docs/user_guide/ASSISTANT_LEVELS.md`,
`docs/qa/MANUAL_TESTING_GUIDE.md`, `docs/development/ARCHITECTURE.md`,
`docs/development/v0.1/DESIGN_V010.md`. Ссылки ведут в никуда (404 на
GitHub).

**Причина.** Эти документы относятся к T-026 (документация MVP) и
T-019/DESIGN — их создание запланировано позже. `docs/README.md` в
итерации 1 создаётся уже с честной пометкой «планируется».

**Обход / текущее поведение.** Навигационный индекс `docs/README.md`
разделяет «существующие» и «планируемые» документы; README пока не
поправлен.

**План закрытия.** Создать документы в T-026 и обновить таблицу в
`README.md`; либо сразу заменить ссылки на пометку «(T-026)» при
следующей правке README.
