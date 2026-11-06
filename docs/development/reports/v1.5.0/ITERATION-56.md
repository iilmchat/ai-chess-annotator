# Итерация 56 — Fix CI Pipeline & Backlog Update (CI, T-123)

**Дата:** 2026-11-06
**Статус:** ⚠️ частично — исправление CI и T-123 готовы и проверены
локально (Windows) и в Linux-контейнере, имитирующем backend-job; зелёный
прогон GitHub Actions **не подтверждён**: workflow блокируется на уровне
аккаунта (billing), до запуска шагов дело не доходит. См. §5.
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` = `81f5e61`. CI run
[41651192791](https://github.com/iilmchat/ai-chess-annotator/actions/runs/41651192791)
(`backend`) — красный: **11 failed**, 1269 passed, 40 skipped, 1 xfailed.
Все 11 падений — `POST /v1/analyze` → 500
`{"detail":"Engine unavailable","code":"engine_not_found"}`:
`tests/integration/test_api_games.py` (2 теста) и
`tests/integration/test_streaming.py` (все 9 тестов).

**Найденная причина (точнее, чем в постановке).** Stockfish в CI **был
установлен**, но не находился приложением. Пакет Debian/Ubuntu кладёт
бинарник в `/usr/games/stockfish`, а дефолт `Settings.stockfish_path` —
`/usr/bin/stockfish`; `app/engine/service.py::_resolve_stockfish_path`
проверяет `Path(raw).is_file()` и `shutil.which(raw)`, не находит и
бросает `EngineNotFoundError`. Остальные engine-тесты в CI «тихо»
скипались (`requires_engine` / `skipif`), поэтому красными были только
11 тестов без защиты. Постановка «Stockfish не установлен» верна лишь
частично: он установлен, но не по ожидаемому пути.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `docs/development/reports/v1.5.0/ITERATION-56.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `.github/workflows/ci.yml` | В env шага `Pytest` (job `backend`) добавлен `STOCKFISH_PATH: /usr/games/stockfish` (+ комментарий, почему путь именно такой). Шаг `Install Stockfish` (`apt-get install -y stockfish libcairo2`) не менялся — он корректен |
| `tests/integration/test_api_games.py` | Модульный маркер `requires_engine = pytest.mark.skipif(not _engine_available(), …)`; навешен на 2 теста, дергающих `/v1/analyze` (`test_get_game_returns_saved_analysis_and_commentaries`, `test_list_games_contains_saved_game`); существующий inline-skipif у `test_get_game_moves_match_analyze_deeply` заменён на тот же маркер (поведение не изменилось) |
| `tests/integration/test_streaming.py` | `import shutil` + `from pathlib import Path`, helper `_engine_available()`; `pytestmark` расширен до списка: первым DB-guard, вторым engine-guard (все 9 тестов файла начинают с `/v1/analyze`) |
| `TODO.md` | Backlog: добавлена **T-123** «Cloud Deployment via Reverse SSH GPU Tunnel» (+14 строк, после T-122) |
| `docs/development/RELEASES.md` | Запись «Итерация 56 — Fix CI Pipeline & Backlog Update» |
| `docs/CHANGELOG.md` | `[Unreleased] → Fixed`: устранён `engine_not_found` в CI, engine-тесты больше не скипаются молча |
| `docs/KNOWN_ISSUES.md` | KI-006 (остаётся Open): в «Обход / текущее поведение» добавлен путь `/usr/games/stockfish` и env-переменная CI |

Коммиты: `cbaa9d6` (fix CI + тесты), `54dbc78` (T-123), запушены в
`origin/main`.

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| CI: путь к движку | backend-job (pytest с `STOCKFISH_PATH=/usr/games/stockfish`) | 11 ранее падавших тестов выполняются на реальном Stockfish, а не скипаются |
| Целевые файлы | `tests/integration/test_api_games.py`, `tests/integration/test_streaming.py` | 17 тестов: **17 passed** локально (Windows, real Stockfish + PostgreSQL) |
| Guard без движка | маркеры модулей | при `STOCKFISH_PATH=<несуществующий>` оба модуля скипаются (проверено); при реальном движке — выполняются |
| Ранее скипавшиеся engine-тесты | `test_api_analyze` (4), `test_llm_calls_api` (11), `test_usage_daily` (5), `tests/unit/test_engine.py` (5), `test_api_games` (1), `test_data_isolation` (1), `test_engine_cache` (1) | 28 из 40 прежних skip теперь выполняются на Ubuntu-CI впервые; в Linux-контейнере — зелёные |
| Регресс | полная сюита | Windows: **1305 passed, 15 skipped, 1 xfailed**; Linux-контейнер (CI-подобно): **1311 passed, 9 skipped, 1 xfailed** |

Новых unit-тестов не добавлялось: изменение — инфраструктурное
(skip-guard + env), поведение покрыто существующими тестами, которые
раньше не запускались в CI.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** Windows — **1305 passed, 15 skipped, 1 xfailed** (125.40 с,
exit 0); Linux-контейнер — **1311 passed, 9 skipped, 1 xfailed** (90.09 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита (Windows) | `pytest -q` | **1305 passed, 15 skipped, 1 xfailed in 125.40 s** (exit 0) |
| Полная сюита (Linux-контейнер, CI-подобно) | `pytest -q` после `apt-get install stockfish libcairo2` + `pip install -e ".[dev]"` + `alembic upgrade head` | **1311 passed, 9 skipped, 1 xfailed in 90.09 s** (exit 0) |
| Целевые файлы | `pytest tests/integration/test_api_games.py tests/integration/test_streaming.py -q` | **17 passed in 22.75 s** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| YAML CI | разбор `ci.yml` | валиден; env Pytest-шага = `{POSTGRES_HOST, POSTGRES_PORT, REDIS_URL, STOCKFISH_PATH}` |
| GitHub Actions | run 42180395309 | ❌ **failure по billing**: все 4 job упали за 1–2 с, `steps: []` |

**GitHub Actions (блокер, не код).** Run
[42180395309](https://github.com/iilmchat/ai-chess-annotator/actions/runs/42180395309)
(attempts 1 и 2) — все job-ы (`backend`, `frontend`, `e2e`, `docker`)
завершились за 1–2 секунды без единого шага. Аннотация check-run:
*«The job was not started because recent account payments have failed or
your spending limit needs to be increased. Please check the 'Billing &
plans' section in your settings»*. Логов job-ов не существует
(`BlobNotFound`). Фронтенд/докер-джобы эти изменением не затрагивались и
в прошлом прогоне были зелёными, так что мгновенный отказ всех четырёх —
аккаунтная проблема, а не регресс workflow.

---

## 4. Smoke

1. **Причина `engine_not_found` воспроизведена и устранена.** В
   Linux-контейнере (`python:3.12-slim`) `apt-get install stockfish`
   ставит **`/usr/games/stockfish`** (78 749 168 байт), а дефолт
   приложения — `/usr/bin/stockfish`; с `STOCKFISH_PATH=/usr/games/stockfish`
   тот же набор команд, что в CI, проходит: `alembic upgrade head` →
   `pytest -q` → **1311 passed, 9 skipped, 1 xfailed** за 90 с.
2. **Guard без движка.** С `STOCKFISH_PATH`, указывающим на
   несуществующий файл, `tests/integration/test_streaming.py` получает
   `pytestmark = [db=False, engine=True]` и скипается с reason
   «Stockfish не найден (KI-006)»; `requires_engine` в
   `test_api_games.py` — `skipif(True)`. При реальном движке оба guard-а
   ложны, тесты выполняются.
3. **Прежние 11 падений.** `17 passed` для двух целевых файлов локально
   (real Stockfish + PostgreSQL) — включая 9 streaming-тестов и оба
   analyze-зависимых `test_api_games`.
4. **T-123.** `git show 54dbc78` — единственный файл `TODO.md`, блок
   вставлен в `## Backlog (после MVP)` после T-122, до `---` перед
   «Как обновлять этот файл»; ID уникален; формат совпадает с T-114…T-122.
5. **Scope.** `git diff --name-only 81f5e61..HEAD` — ровно 4 ожидаемых
   файла кода/тестов/TODO; рабочее дерево чистое; ветка синхронна с
   `origin/main`.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| **GitHub Actions заблокирован billing-ом** | Все job-ы падают до первого шага: «recent account payments have failed or your spending limit needs to be increased». Это не код: исправление не может быть подтверждено зелёным бейджем, пока в GitHub Settings → Billing & plans не устранён платёж/лимит; после этого нужен `gh run rerun` / новый push |
| Skip-guard маскирует отсутствие движка | Если путь к Stockfish снова станет неверным, engine-тесты молча скипнутся и CI останется «зелёным» — тот же класс ошибки, что и в этой итерации. Рекомендация: отдельный CI-шаг «движок обязателен» (`/v1/health` → `stockfish: ok` или вызов `_resolve_stockfish_path`) |
| Впервые в CI выполнились 28 engine-тестов | Они годами скипались; локально/в Linux-контейнере зелёные, но на 2-vCPU GitHub-раннере чувствительны к таймингам: `test_club_analysis_within_one_second` (лимит 1.15 с, локально 0.79–0.82 с), SLA 30 с для 53 полуходов (локально 13.2 с), ratio-проверка кэша (`second < first*0.5`), polling с таймаутами 5 с в `test_llm_calls_api`/`test_usage_daily`. Наблюдать на первом настоящем прогоне |
| Локально и в CI разное число skip | Windows: 15 skipped (в т.ч. `test_token_budget_api` — PostgreSQL/Redis «недоступны» при локальном `.env`), Linux-контейнер: 9 skipped (только LM Studio/DeepSeek/web-dist). Это окружение, не регресс |
| Миграция раннера `ubuntu-latest` → Ubuntu 26 | Аннотация GitHub: с 19.10.2026 `ubuntu-latest` мигрирует; пакет Stockfish и путь `/usr/games/stockfish` там сохраняются, но версия движка может измениться |
| `.env.example` по-прежнему `STOCKFISH_PATH=/usr/bin/stockfish` | Для Ubuntu это неверный путь; `GETTING_STARTED.md` уже документирует `/usr/games/stockfish`. Шаблон не менялся, чтобы не ломать Windows/macOS-инструкции |
| KI-006 остаётся **Open** | Бинарник по-прежнему вне репозитория; закрытие — `scripts/install-stockfish.ps1` + раздел `GETTING_STARTED.md` (обновлён текст обхода) |

---

## 6. Новые KI

**Нет.** Это исправление существующего дефекта конфигурации CI, а не
новая проблема. **KI-006** остаётся **Open** (Low) — обновлён только
раздел «Обход / текущее поведение»: добавлены путь
`/usr/games/stockfish` и CI-переменная `STOCKFISH_PATH`. Заблокированный
billing GitHub Actions — внешнее состояние аккаунта, не дефект проекта,
поэтому KI не заводится; статус итерации отражён здесь и в
`RELEASES.md`.

---

## 7. Обоснование

* **Почему env-переменная, а не смена дефолта в `app/config.py`.**
  Дефолт `/usr/bin/stockfish` — документированный контракт для
  Debian-подобных систем и `.env.example`; менять его ради CI означало бы
  трогать продовый код ради тестовой среды. Явный
  `STOCKFISH_PATH=/usr/games/stockfish` в шаге `pytest` точечен, обратим
  и совпадает с тем, как проект уже конфигурирует движок локально (через
  `.env`). Pydantic-Settings даёт env-переменной приоритет над `.env`, а в
  CI `.env` отсутствует (gitignored) — источник значения однозначен.
* **Почему оставлены skip-guard-ы.** Локальная разработка (Windows/macOS)
  и любые окружения без движка не должны падать на 500 `engine_not_found`
  в тестах, которые проверяют SSE/историю, а не Stockfish. Guard
  повторяет уже принятый в `test_engine.py` паттерн (KI-006) и делает
  поведение единообразным во всех трёх файлах. Обратная сторона —
  возможность «тихого» skip в CI; поэтому в §5 рекомендуется отдельный
  обязательный CI-шаг проверки движка (кандидат в hardening T-005).
* **Почему не мокали движок в этих тестах.** Smoke-ценность
  интеграционных тестов — в реальном UCI-пайплайне (`/v1/analyze` →
  партия → БД → SSE); мок Stockfish скрыл бы ровно тот класс ошибок,
  который искали. Дешевле оказалось починить конфигурацию CI (одна
  переменная) и включить реальный движок — заодно впервые выполняются 28
  ранее скипавшихся engine-тестов.
* **Почему T-123 только в Backlog.** Задача описывает будущую
  архитектуру (обратный SSH-туннель, `BackendRouter` с circuit-breaker,
  fallback на DeepSeek), её DoD требует кода, конфига и тестов; в рамках
  этой итерации она не реализуется — поэтому зафиксирована как
  backlog-пункт с зависимостью T-013 и полным DoD, чтобы не потеряться.
* **Почему «частично», а не «завершена».** RULES §6.3 требует зелёного
  CI. Код и тесты проверены тремя независимыми способами (Windows-сюита,
  Linux-контейнер, независимый ревьюер), но фактический GitHub-прогон
  заблокирован на уровне аккаунта. Честный статус — ⚠️ частично, с явным
  внешним блокером и готовым планом действий.
