# Итерация 25 — LoRA Fine-tuning, Playwright E2E и RAG-коллекция (T-104, T-036b, T-042)

**Дата:** 2026-10-29
**Статус:** ✅ завершена в объёме DoD. **KI-030 остаётся Open** (реальное
обучение LoRA не выполнялось — на машине нет GPU/CUDA); заведены KI-039,
KI-040. T-104, T-036b и T-042 закрыты.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Backend / скрипты — создано

| Файл | Назначение |
| :--- | :--- |
| `docs/development/reports/LORA_TRAINING.md` | Инструкция по LoRA: prerequisites, экспорт, схема, гиперпараметры, dry-run, RunPod/Colab, стоимость, сохранение `models/lora-club-v1`, vLLM `--lora-modules`, eval, ограничения |
| `tests/fixtures/lora_sample.jsonl` | Синтетический датасет (4 записи, `system→user→assistant`, `level=club`) для офлайн dry-run |

### Backend / скрипты — изменено

| Файл | Что изменилось |
| :--- | :--- |
| `scripts/train_lora.py` | Каркас → реальный SFT: peft/transformers, QLoRA 4-bit, r=16/alpha=32/`q_proj,v_proj`, 3 эпохи, lr=2e-4, gradient checkpointing, маскирование лосса только на assistant, right-padding-коллатор; `--dry-run` без torch, `--check-env`, `--system-prompt`, `--limit`, `--target-modules`; понятные `SystemExit` (нет зависимостей / нет CUDA / нет, пуст или невалиден датасет / CUDA OOM); статистика записей/уровней/токенов; `--output-dir` по умолчанию `models/lora-club-v1` |
| `scripts/evaluate_llm.py` | T-042: `--source SOURCE` — фильтр по `annotated_games.source` в `_load_games()` (фиксирует eval-набор `new_samples`/`my-collection` для сравнения LoRA «до/после»); вывод источника в логе |
| `tests/integration/test_llm_evaluations.py` | T-042: тест `test_load_games_filters_by_source` (фильтр по source, пустой результат для неизвестного source) |
| `scripts/export_dataset.py` | Схема JSONL теперь строгая `system → user → assistant`; `system` — `app/prompts/<level>.txt` через `importlib.resources` (`load_system_prompt`, fallback club, override), метаданные/`schema_version=1` сохранены |
| `app/config.py` | `lora_model` возвращает basename адаптера **только** для существующего пути (файл или каталог с `adapter_config.json`), иначе `vllm_model`; добавлены `lora_adapter_dir`, `lora_adapter_available` |
| `app/llm/factory.py` | Локальные уровни используют `cfg.lora_model`; structlog `info llm_lora_adapter_enabled` / `warning llm_lora_adapter_unavailable`; маршрут `gm` (DeepSeek) не изменён |
| `.env.example` | Секция LoRA: проверка существования пути/`adapter_config.json`, vLLM `--lora-modules`, ссылка на `LORA_TRAINING.md` |
| `.gitignore` | `models/` (артефакты обучения), `web/test-results/`, `web/playwright-report/`, `web/blob-report/` |
| `tests/unit/test_train_lora.py` | 41 тест: строгий валидатор, `read_records`/коды ошибок, маскирование labels на фейковом tokenizer (+ truncation, fallback без chat_template), `collate_batch`, статистика, парсер/дефолты, `_prepare_model` (r/alpha/target_modules/4bit/gradient checkpointing), `_is_oom_error`, no-deps/no-CUDA, dry-run/`--check-env`/`--system-prompt` |
| `tests/unit/test_dataset_export.py` | 6 тестов: `system→user→assistant`, промпт из `club.txt`, override, метаданные, `schema_version` |
| `tests/unit/test_lora_config.py` | 13 тестов: существующий/несуществующий адаптер, файл, каталог без `adapter_config.json`, фабрика (club/beginner/gm), structlog |
| `tests/fixtures/README.md` | Описание `lora_sample.jsonl` |

### Frontend / E2E — создано

| Файл | Назначение |
| :--- | :--- |
| `web/playwright.config.ts` | `testDir ./e2e`, projects `chromium` (Desktop Chrome) + `mobile` (Pixel 7), baseURL `http://127.0.0.1:5173`, webServer Vite, `trace on-first-retry`, `screenshot only-on-failure`, `video retain-on-failure`, reporter list+html, retries CI?1:0, `reuseExistingServer: !CI` |
| `web/e2e/fixtures.ts` | page.route-моки API (контракты `web/src/api.ts`/`types.ts`), `mockApi`, `registerAndLogin`, `uniqueEmail` (`@example.com`), SSE-хелпер, `E2E_REAL_BACKEND`-переключатель |
| `web/e2e/auth.spec.ts` | Регистрация → вход → email в шапке → «Выйти» |
| `web/e2e/analyze.spec.ts` | Анализ PGN → доска + MoveList → `/analysis/:id` |
| `web/e2e/commentary.spec.ts` | SSE-генерация → «Редактировать» → PATCH `/v1/commentaries/:id` → `edit #id` |
| `web/e2e/history.spec.ts` | «История» → `/history` → клик по партии → `/analysis/:id` |
| `web/e2e/limits.spec.ts` | usage-badge `Free: 0/50k` → модалка дневного лимита |
| `web/e2e/README.md` | Режимы (mocked/real), моки, селекторы, CI, артефакты |

### Frontend — изменено

| Файл | Что изменилось |
| :--- | :--- |
| `web/package.json` | devDep `@playwright/test 1.64.1`; скрипты `test:e2e`, `test:e2e:ui` |
| `web/pnpm-lock.yaml` | +`@playwright/test`, `playwright`, `playwright-core` |
| `web/vite.config.ts` | `test.exclude: [...configDefaults.exclude, "e2e/**"]` (vitest не подхватывает E2E) |
| `.github/workflows/ci.yml` | Новый job `e2e`: pnpm/node, `playwright install --with-deps chromium`, `playwright test`, `upload-artifact@v4 if: failure()` (`web/test-results/`, `web/playwright-report/`, retention 7 дней) |

### Документация

`docs/KNOWN_ISSUES.md` (KI-030 — прогресс без обучения, +KI-039/KI-040,
сводка 40/23/17), `docs/CHANGELOG.md`, `docs/development/RELEASES.md`,
`README.md` (статус, тесты 435/36, LoRA/E2E/KI/CI), `TODO.md` (T-104,
T-036b, T-042), `docs/development/reports/LLM_EVAL.md` (T-042: состав
RAG-коллекции 624, eval-команда `--source new_samples`), этот отчёт.

### Данные (T-042)

Новые PGN-файлы загружены в `annotated_games` (RAG) — таблица ниже;
`data/` в git не коммитится, поэтому в репозитории меняются только
скрипт/доки/тесты, а сами данные лежат в БД (temp Postgres 5435).

---

## 2. Покрытие

| Область | Файл | Тесты |
| :--- | :--- | ---: |
| LoRA: валидатор, маскирование, CLI, конфиг | `tests/unit/test_train_lora.py` | 41 |
| Формат JSONL/экспорт | `tests/unit/test_dataset_export.py` | 6 |
| LoRA в конфиге/фабрике | `tests/unit/test_lora_config.py` | 13 |
| Всего новых/обновлённых unit | — | **60** (на HEAD было 7) |
| E2E браузерные сценарии | `web/e2e/*.spec.ts` | **5 сценариев × 2 проекта = 10 прогонов** |
| Регрессия backend | вся сюита | **435 passed, 1 skipped, 1 xfailed** |
| Регрессия frontend | Vitest | **36 passed** (6 файлов) |
| Eval source filter (T-042) | `tests/integration/test_llm_evaluations.py` | **+1** тест (файл: 2 passed) |

Проверяемые сценарии T-104:

- **Схема:** ровно `system → user → assistant`; битый JSON, перестановка
  ролей, пустой `content`, неизвестная `schema_version`, лимиты
  (50 000 символов / 16 сообщений) — ошибки; пустой датасет/файл — exit 1.
- **Маскирование:** `labels=-100` на system/user и служебных токенах
  шаблона; обучаются только токены assistant; truncation по
  `max_length`; prompt не оставляет места ответу → `ValueError`.
- **Окружение:** `--dry-run` без `torch`; реальный запуск без
  зависимостей → exit 1 с инструкцией; no-CUDA и OOM ветки — unit-тесты
  через подмену модулей.
- **Конфиг:** несуществующий путь → базовая модель; каталог с
  `adapter_config.json` → basename адаптера; каталог без маркера → базовая
  модель; фабрика использует `cfg.lora_model`.

Проверяемые сценарии T-036b:

- `auth`: приватный маршрут без сессии → `/login`; регистрация через UI,
  редирект на `/`; email в шапке; «Выйти» чистит сессию и возвращает
  `/login`.
- `analyze`: клик «Разобрать партию» → URL `/analysis/:id`, доска,
  список ходов, сводка.
- `commentary`: «Сгенерировать» → SSE-поток → текст → «Редактировать» →
  «Сохранить правку» → реальный `PATCH /v1/commentaries/:id` (200) →
  `edit #55`.
- `history`: «История» в шапке → `/history` → клик по строке партии →
  `/analysis/:id` с доской.
- `limits`: usage-badge `Free: 0/50k` в шапке → модалка «Дневной лимит
  токенов» (`Использовано 0 из 50k…`) → «Закрыть».

---

## 3. Результаты

**Build:** **0 errors / 0 warnings** (backend + frontend). **Tests:**
**435/437 passed** (backend, 1 skipped, 1 xfailed), **36/36** frontend,
**10/10** E2E mocked, **5/5** E2E real-backend. **RAG:** в
`annotated_games` **624** записи (102 baseline `my-collection` + **522**
новых `new_samples`).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Backend-сюита (чистая БД) | `pytest -q` (POSTGRES_PORT=5435, чистая `chessannotator_verify`, Redis 6380, STOCKFISH_PATH) | **435 passed, 1 skipped, 1 xfailed, 2 warnings in 256.59s**, exit 0; collected 437. Skip — нет `DEEPSEEK_API_KEY`, xfail — KI-012; **+53** к итерации 24 (382) |
| LoRA-набор | `pytest tests/unit/test_train_lora.py tests/unit/test_dataset_export.py tests/unit/test_lora_config.py -q` | **60 passed** (41+6+13) |
| ruff | `ruff check .` | **All checks passed!** (exit 0) |
| mypy | `mypy app` | **Success: no issues found in 67 source files** (exit 0) |
| LoRA dry-run | `python scripts/train_lora.py --dry-run --dataset tests/fixtures/lora_sample.jsonl` | exit 0: записей 4, `club=4`, ~880 токенов (сред. 220, макс. 225), r=16/alpha=32/`q_proj,v_proj`/epochs=3/lr=0.0002/4bit=да |
| LoRA без GPU | `python scripts/train_lora.py --dataset tests/fixtures/lora_sample.jsonl` | exit 1, понятная инструкция («нет зависимостей… RunPod/Colab… LORA_TRAINING.md»), без голого traceback |
| LoRA env | `python scripts/train_lora.py --check-env` | exit 0: torch/transformers/peft/datasets/accelerate/bitsandbytes/trl — НЕТ; `cuda: нет`; «доступен только --dry-run» |
| E2E mocked | `pnpm --dir web run test:e2e` | **10 passed (10.5 s)** = 5 сценариев × chromium/mobile; backend не нужен |
| E2E real backend | `E2E_REAL_BACKEND=1 VITE_API_PROXY=http://127.0.0.1:8011 pnpm --dir web run test:e2e -- --project=chromium` | **5 passed (1.4 мин)**; commentary ~1.3 мин (реальная SSE-генерация через LM Studio), analyze 15.3 с, history 20.3 с, auth/limits ~4 с |
| Vitest | `pnpm --dir web test` | **36 passed** (6 файлов), e2e не подхватываются |
| Frontend typecheck | `pnpm --dir web typecheck` | exit 0 |
| Frontend build | `pnpm --dir web build` | exit 0: 99 модулей, 6.33 с; JS 465.15 КБ (gzip 146.41), CSS 14.29 КБ (gzip 3.77), dist 480 186 B |
| CI YAML | `yaml.safe_load(.github/workflows/ci.yml)` | OK; jobs: `backend`, `frontend`, `e2e`, `docker`; артефакты e2e проверены на существование: `web/test-results/`, `web/playwright-report/index.html` |
| Import-контракты | `pytest tests/unit/test_llm.py tests/unit/test_prompts.py tests/integration/test_generation.py -q` | **30 passed**, exit 0 |
| RAG ingest `sample3.pgn` (T-042) | `python scripts/ingest_pgn.py --file data/sample3.pgn --limit 1000 --source new_samples` | exit 0: **500** партий, ID 103..602 |
| RAG ingest `sample4.pgn` (T-042) | `... --file data/sample4.pgn --limit 1000 --source new_samples` | exit 0: **13** партий, ID 603..615 |
| RAG ingest `sample5.pgn` (T-042) | `... --file data/sample5.pgn --limit 1000 --source new_samples` | exit 0: **9** партий, ID 616..624 |
| Итог `annotated_games` (T-042) | `SELECT count(*), count(annotation_text), count(*) FILTER (WHERE embedding IS NULL)` | **624** / **621** с аннотациями / **0** NULL embedding; по source: `my-collection`=102, `new_samples`=522 |
| Eval-фильтр (T-042) | `python scripts/evaluate_llm.py --help` + `_load_games(source=...)` | `--source` присутствует; `new_samples` → 3/3 партии, `my-collection` → 3/3, неизвестный source → 0; `pytest tests/integration/test_llm_evaluations.py -q` → **2 passed** |
| Ruff/mypy после T-042 | `ruff check scripts/evaluate_llm.py tests/integration/test_llm_evaluations.py`; `mypy app` | All checks passed; no issues in 67 files |

---

## 4. Smoke (ручной сценарий)

**4.1. LoRA dry-run (локально, без GPU).**

```powershell
.\.venv\Scripts\python.exe scripts/train_lora.py --dry-run `
    --dataset tests/fixtures/lora_sample.jsonl
# → exit 0
# Записей: 4; Распределение по уровням: club=4
# Оценка токенов: всего ~880, в среднем ~220, максимум ~225
# Гиперпараметры: r=16, alpha=32, target_modules=q_proj,v_proj,
#   epochs=3, batch=1, grad_accum=8, lr=0.0002, max_length=2048,
#   4bit=да, gradient_checkpointing=да
# DRY-RUN OK: схема JSONL корректна, статистика посчитана,
#   обучение не запускалось.

.\.venv\Scripts\python.exe scripts/train_lora.py `
    --dataset tests/fixtures/lora_sample.jsonl
# → exit 1 (ожидаемо: нет torch/CUDA)
# Нет зависимостей для обучения LoRA: ... pip install torch transformers
# peft datasets accelerate bitsandbytes
# Инструкция: docs/development/reports/LORA_TRAINING.md
```

**4.2. Реальный backend (smoke через HTTP, `APP_ENV=dev` → JWT обязателен).**

Сервер: `uvicorn app.main:app --host 127.0.0.1 --port 8011` (temp Postgres
5435/`chessannotator_e2e`, Redis 6380, Stockfish
`data/tools/stockfish/...exe`, LM Studio 8034, `TOKEN_BUDGET_ENABLED=true`,
`RATE_LIMIT_ENABLED=false`). Health:
`{"status":"degraded","components":{"db":"ok","redis":"ok","stockfish":"ok","llm":"skipped"}}`
(`degraded` — llm `skipped` при выключенном прогреве, ожидаемо).

```bash
POST /auth/register {"email":"smoke-…@example.com","password":"…"}  # 201
POST /auth/login                                                    # 200, access+refresh
GET  /auth/me                       Authorization: Bearer <access>  # 200
GET  /v1/users/me/usage                                            # 200 {"tier":"free","used_tokens":0,"limit_tokens":50000,"enabled":true}
POST /v1/analyze {"pgn":"1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7","level":"club"}
# → 200, analysis_id=1, 10 полуходов, time_taken_ms=3605
GET  /v1/games                                                     # 200, 1 партия (только своя)
GET  /v1/games/1                                                   # 200, 10 ходов
POST /v1/commentary/stream {"analysis_id":1,"level":"club"}
# → 200 text/event-stream: event meta (qwen/qwen3-4b-2507), delta ×N, done;
#   wall ~52 с (локальная LM Studio ~9 ток/с)
```

**4.3. E2E real-backend (Playwright).** `E2E_REAL_BACKEND=1
VITE_API_PROXY=http://127.0.0.1:8011 pnpm --dir web run test:e2e --
--project=chromium` → **5 passed (1.4 мин)**: auth (живая
регистрация `@example.com`), analyze (реальный Stockfish), commentary
(реальная SSE-генерация LM Studio + реальный `PATCH` в БД), history
(реальный `GET /v1/games`), limits (usage-badge из backend,
`Free…/50k`). Это подтверждает, что page.route-моки не «зашиты» в
сценарии и что переключатель работает.

**4.4. E2E mocked (CI-режим).** `pnpm --dir web run test:e2e` → 10 passed
за 10.5 с при закрытом порту 8000: все запросы перехвачены
`page.route`, backend/Postgres/Stockfish/LLM не нужны.

**4.5. RAG ingest новых PGN (T-042).** Temp Postgres 5435
(`chessannotator`), миграции `alembic upgrade head`, эмбеддинги — LM Studio
8034 (`text-embedding-nomic-embed-text-v1.5`, 768). Сначала воспроизведён
baseline итерации 12 (`my-collection`, `--limit 50`):

```powershell
python scripts/ingest_pgn.py --file data/sample.pgn  --limit 50 --source my-collection  # 10
python scripts/ingest_pgn.py --file data/sample1.pgn --limit 50 --source my-collection  # 42
python scripts/ingest_pgn.py --file data/sample2.pgn --limit 50 --source my-collection  # 50
# COUNT(*) = 102, annotation_text = 99, NULL embedding = 0  ← совпало с историей

python scripts/ingest_pgn.py --file data/sample3.pgn --limit 1000 --source new_samples  # 500, ID 103..602
python scripts/ingest_pgn.py --file data/sample4.pgn --limit 1000 --source new_samples  # 13,  ID 603..615
python scripts/ingest_pgn.py --file data/sample5.pgn --limit 1000 --source new_samples  # 9,   ID 616..624

SELECT count(*), count(annotation_text), count(*) FILTER (WHERE embedding IS NULL)
  FROM annotated_games;
-- 624 | 621 | 0
```

Итог: **624 записи = 102 baseline + 522 новых**; `my-collection` — 1..102,
`new_samples` — 103..624. Проверка eval-набора:

```bash
python scripts/evaluate_llm.py --help   # есть --source
# _load_games(source="new_samples")   → 3 партии (ID 370, 241, 173 — самые короткие)
# _load_games(source="my-collection") → 3 партии (ID 51, 16, 32)
# _load_games(source="no-such")       → 0
```

`pytest tests/integration/test_llm_evaluations.py -q` → **2 passed**
(включая новый тест фильтра по source).

---

## 5. Что не так

1. **KI-030 — не закрыт, обучения LoRA не было.** На машине нет NVIDIA
   GPU/CUDA и `torch`/`peft`; реальный GPU-прогон и замер прироста
   качества не выполнялись, поэтому **средняя оценка LLM не изменилась
   (`club` 4.40/10)**. Собраны: реальный train-пайплайн, схема датасета,
   `LORA_ADAPTER_PATH` в конфиге/фабрике, инструкция
   `LORA_TRAINING.md` с RunPod/Colab и стоимостью. Это соответствует DoD
   T-104 («не блокировать итерацию, если нет GPU»), но **качество 7/10
   не достигнуто**; KI-030 остаётся **Open**, достижимость через LoRA —
   гипотеза, которую нужно проверить на GPU.
2. **Дизайн SFT-пары (KI-039).** `export_dataset.py` учит пару
   `original_text → edited_text`, то есть **переписывать** готовый
   комментарий, тогда как на инференсе модель получает «пакет фактов».
   Для роста `factual_accuracy` (главная слабая шкала KI-030) это может
   дать меньше, чем SFT на `fact_sheet → commentary`. Нужно решение до
   первого GPU-прогона: `schema_version=2` с сохранением пакета фактов
   либо осознанная rewrite-постановка.
3. **E2E в CI — на моках (KI-040).** Реальный backend в CI не
   поднимается (нужны Postgres/Redis/Stockfish/LLM); реальные контракты
   покрыты pytest-интеграцией, а сквозной браузерный прогон против
   настоящего API выполнен только локально (5 passed). Моки повторяют
   контракты, но не заменяют сквозную проверку.
4. **`typecheck` не покрывает `web/e2e/**` и `playwright.config.ts`**
   (`web/tsconfig.json` → `include: ["src"]`); Playwright транспилирует
   без `tsc`. Риск: типовые ошибки в E2E-коде поймает только сам
   Playwright-прогон. Кандидат — отдельный `tsconfig.e2e.json`.
5. **Один «длинный» пример останавливает обучение.** Если prompt не
   оставляет места assistant-ответу в `--max-length`, `train()` кидает
   `SystemExit` и весь прогон завершается (all-or-nothing), а не
   пропускает запись. В `LORA_TRAINING.md` это зафиксировано.
6. **`--load-in-4bit` включён по умолчанию.** Явного `--load-in-4bit` в
   примерах можно не писать; на GPU без `bitsandbytes` пользователь
   получит ошибку импорта — инструкция в траблшутинге.
7. **Real-backend E2E медленный.** Один комментарий через локальную
   4B-модель — ~50–60 с, прогон chromium — ~1.4 мин; поэтому real-режим
   не годится для PR-CI без stub-LLM (KI-040).
8. **`--limit` и `--system-prompt`** покрыты unit-тестами, но в живом
   GPU-прогоне не проверялись (нет GPU).
9. **`tests/fixtures/lora_sample.jsonl` — синтетика**, не реальные
   `edits`; реальный экспорт (`--level club`) требует PostgreSQL и
   накопленных правок (в temp-БД их нет), поэтому проверен unit-тестами
   `record_from_edit`/валидатора, а не живой выгрузкой.
10. **`README`-бейджи тестов обновляются вручную** — возможен дрейф при
    следующей итерации (не автоматизировано).
11. **T-042: рабочая БД с baseline 102 недоступна в текущем окружении.**
    Порт Postgres 5434 занят чужим контейнером (`dzenwriter-postgres-1`),
    `.env`/постоянного volume проекта нет (итерация 24 работала на temp
    Postgres). Поэтому ingest выполнен в temp Postgres `chess-iter25-pg`
    на `127.0.0.1:5435`, а baseline 102 **воспроизведён** историческим
    рецептом итерации 12 (`sample.pgn --limit 50` → 10, `sample1.pgn`
    `--limit 50` → 42, `sample2.pgn --limit 50` → 50). Итог 624 совпадает
    с ожидаемым `102 + 522`. Для применения к рабочей БД нужно повторить
    три команды ingest с её `POSTGRES_PORT`/кредами; контейнер с данными
    оставлен запущенным (`chess-iter25-pg`, порт 5435), иначе 522 новых
    записи существовали бы только в удалённой temp-БД.
12. **T-042: eval-набор по умолчанию изменился.** `evaluate_llm.py` без
    `--source` берёт «самые короткие партии из всей коллекции»; после
    ingest-а 522 новых партий в набор попадут более короткие `new_samples`.
    Для сравнения «до/после LoRA» обязательно фиксировать
    `--source new_samples` и `--games` (добавлено в T-042, описано в
    `LLM_EVAL.md`).

---

## 6. Новые KI

| KI | Severity | Модули | Суть |
| :--- | :--- | :--- | :--- |
| **KI-039** | Med | llm, dataset | SFT-пара LoRA учит «переписать комментарий» (original→edited), а не «сгенерировать из пакета фактов»; до GPU-прогона нужна schema_version=2 (`fact_sheet → commentary`) или осознанная rewrite-постановка |
| **KI-040** | Low | tests, web, ci | E2E T-036b в CI работают на page.route-моках; реальный backend в CI не поднимается (нет Postgres/Stockfish/LLM), real-режим проверен только локально |

**KI-030** — остаётся **Open**: в итерации 25 собран LoRA-пайплайн, но
обучения и замера качества не было (нет GPU); зафиксирован прогресс и
план закрытия (GPU-прогон → `evaluate_llm.py` на 10–20 партиях).
**KI-035** (итерация 24) остаётся Closed.

**T-042:** новых KI нет. Ограничение «рабочая БД недоступна» — окружения,
а не кода: ingest воспроизводим командами из `LLM_EVAL.md`, baseline 102
восстановлен по историческому рецепту.

Сводка реестра: **40** записей, 23 Open, 17 Closed.

---

## 7. Обоснование

- **Почему SFT через `peft`/`transformers`, а не `trl`/`unsloth`.**
  Задача требует peft/transformers/trl (или unsloth); выбран минимальный
  стек `transformers.Trainer` + `peft.LoraConfig`, который уже
  документирован и не тянет ещё одну крупную зависимость. Маскирование
  лосса реализовано вручную (`labels=-100` до границы assistant),
  поэтому нет привязки к версии TRL; корректность покрыта unit-тестом на
  фейковом tokenizer (не тавтологичным — ожидаемая граница
  пересчитывается независимо).
- **Почему QLoRA 4-bit по умолчанию.** 4B-модель в bf16 требует ≥24 GB
  VRAM; QLoRA помещается в T4 16 GB (Colab free) / RTX 4090 (RunPod) —
  это делает инструкцию реально исполниемой и дешёвой (~$0.5–2 за
  прогон). `--no-4bit` оставлен для 24+ GB.
- **Почему `system` в датасете.** T-104 требует формат
  `messages(system,user,assistant)`; system из `app/prompts/<level>.txt`
  синхронизирует обучение с инференсным системным промптом. Схема
  по-прежнему `schema_version=1`, но теперь строгая; старые выгрузки
  переэкспортируются (данных в `edits` пока мало).
- **Почему проверка существования `LORA_ADAPTER_PATH`.** Без неё
  мисконфиг (`LORA_ADAPTER_PATH` задан, адаптера нет) приводил бы к
  запросу несуществующей модели и `404 model not found` у vLLM на каждом
  комментарии. Теперь fallback на `VLLM_MODEL` + warning — деградация
  мягкая и наблюдаемая. Маркер готовности — `adapter_config.json`
  (`save_pretrained`), чтобы недокачанный каталог не считался адаптером.
- **Почему E2E по умолчанию на моках.** В CI нет Postgres/Stockfish/
  LLM; реальный backend сделал бы PR-CI медленным (~1.4 мин только
  chromium, плюс инфраструктура) и флейки. Моки через `page.route`
  повторяют контракты `web/src/api.ts` и проверяют именно UI-логику
  (роутинг, guard, auth-store, SSE-парсинг, PATCH-флоу), а контракты
  бэкенда покрыты pytest-интеграцией. При этом реализован и проверен
  real-backend режим (`E2E_REAL_BACKEND=1`) — на случай nightly/
  self-hosted прогона; ограничение зарегистрировано как KI-040.
- **Почему `webServer` — Vite, а не `uvicorn`.** Запуск uvicorn внутри
  `playwright.config.ts` требует БД, миграций и Stockfish на машине
  разработчика и в CI; Vite webServer кроссплатформен (Windows/Ubuntu),
  а API-режим переключается переменной окружения. Это осознанный
  компромисс: mocked-прогон стабилен и быстр, real-режим — отдельная
  ручная/nightly проверка.
- **Почему отдельный CI-job `e2e`, а не шаг в `frontend`.** Изоляция
  времени (Playwright-браузеры, `--with-deps`), независимый таймаут и
  артефакты только при падении; падение E2E не маскирует падение
  typecheck/build. Кэш pnpm store для e2e-job — возможная оптимизация
  (не блокер).
- **Почему `models/` в `.gitignore`.** Адаптер — локальный бинарный
  артефакт обучения (десятки МБ), в репозиторий не коммитится;
  инструкция обещает это пользователю, поэтому паттерн добавлен.
- **Почему реальное обучение не запускалось локально.** Установка
  torch/peft/transformers (~гигабайты) без GPU не даёт проверяемого
  результата (CPU-обучение 4B нереалистично), а DoD T-104 это
  предусматривает. Вместо фиктивного прогона сделаны: реальная
  реализация, офлайн-проверка пайплайна (`--dry-run`), тесты чистой
  логики и инструкция для GPU — так обоснованнее, чем «зелёный»
  dry-run без доказательств.
- **Почему ingest с `--limit 1000`.** Дефолт `--limit 10` (итерация 24) —
  защита от случайной загрузки огромного файла; для осознанной загрузки
  конкретных коллекций лимит задаётся явно. `sample3.pgn` содержит 500
  партий, `sample4` — 13, `sample5` — 9; `--limit 1000` покрывает файлы
  целиком, не читая лишнего.
- **Почему baseline воспроизведён, а не «просто залиты новые».** В
  окружении нет рабочей БД с 102 записями (порт 5434 занят чужим
  контейнером, volume проекта не сохранился). Исторический рецепт
  итерации 12 (`--limit 50` для трёх старых файлов) даёт ровно 102 —
  поэтому итог **624 = 102 + 522** воспроизводим и совпадает с ожиданием
  постановки; иначе отчёт содержал бы несравнимую цифру «522 в пустой
  БД».
- **Почему `--source` в `evaluate_llm.py`, а не «просто документация».**
  `evaluate_llm.py` выбирает «самые короткие партии из всей коллекции»:
  после +522 партий набор бы стихийно изменился, и сравнение LoRA
  «до/после» было бы некорректным. Фильтр `--source new_samples`
  (маленькое изменение + интеграционный тест) фиксирует eval-набор и
  делает обещанное в постановке «включить новые партии в eval»
  воспроизводимым, а не ручным.
