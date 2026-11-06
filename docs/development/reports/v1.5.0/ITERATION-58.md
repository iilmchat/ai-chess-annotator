# Итерация 58 — Eval Freeze & BackendRouter (T-124, T-125, T-123)

**Дата:** 2026-11-06
**Статус:** ✅ завершена — eval-набор заморожен (same-sample сравнение
версий модели), `BackendRouter` local-first с fallback в DeepSeek;
`pytest`/`ruff`/`mypy` зелёные; GitHub Actions разблокирован после снятия
billing-блокера аккаунта и завершил первый прогон итерации **success**
(`42196291092`).
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` = `51d695c` (итерация 57: chat-SSE CJK,
556 записей v4, отчёт). Две задачи итерации: (1) в отчёте итерации 52
зафиксировано, что eval-набор партий меняется от прогона к прогону
(`annotated_games` + «самые короткие»), поэтому дельты v1/v2/v3/baseline —
межвыборочные; нужен фиксированный набор (T-024, KI-023); (2) в backlog
(T-123) описан `BackendRouter` для cloud-развёртывания через reverse
SSH-туннель — local-first с fallback в DeepSeek. Billing-блокер GitHub
Actions (итерации 56–57) устранён на стороне аккаунта: последний прогон
итерации 57 (`42185669234`) завершился **success**.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `tests/fixtures/eval_games.json` | Замороженный eval-набор: 5 PGN Moscow zt 1964, `game_id=-1..-5`, sha256 каждого PGN, `schema_version=1` |
| `app/llm/router.py` | `BackendRouter` (`BaseLLMClient`): local-first fallback, circuit-breaker, `is_unavailable_error`, логи `local_gpu_unavailable` |
| `tests/unit/test_evaluate_llm_frozen.py` | 19 офлайн-тестов: целостность фикстуры (sha256, легальность PGN), загрузчик, tamper-detection, CLI-дефолт, отчёт/JSON |
| `tests/unit/test_llm_router.py` | 24 теста: fallback на ConnectError/Timeout/обёрнутой ошибке, отсутствие fallback на HTTP 4xx, circuit-breaker (open/skip/recover), стрим без дублирования, проброс уровня/`aclose` |
| `docs/development/reports/v1.5.0/ITERATION-58.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `scripts/evaluate_llm.py` | `DEFAULT_FROZEN_SET`, `FrozenEvalSetError`, `_load_frozen_games` (schema/limit/sha256/парсинг PGN), CLI `--frozen-set/--no-frozen-set/--frozen-file`, guard «`--source` только с `--no-frozen-set`», frozen-строки в отчёте, `frozen` в JSON, обновлён docstring |
| `app/llm/factory.py` | `_local_client`/`_deepseek_client`; для `club` — `BackendRouter([local(2с, 0 ретраев), deepseek?])`; `beginner` — прямой локальный, `gm` — прямой DeepSeek; docstring-таблица маршрутизации |
| `app/llm/router.py` → `app/llm/__init__.py` | Ре-экспорт `BackendRouter`, `is_unavailable_error` |
| `app/llm/warmup.py` | Прогрев через `beginner` (прямой локальный), а не `club` — иначе при выключенном GPU он уходил бы в облако |
| `app/config.py` | `LLM_ROUTER_LOCAL_TIMEOUT_S=2.0`, `LLM_ROUTER_COOLDOWN_S=60.0` (в итерации 59 таймаут переименован в `LLM_LOCAL_TIMEOUT_S`, добавлен `LLM_LOCAL_BACKEND_URL`) |
| `.env.example` | Те же две переменные с комментариями |
| `tests/unit/test_llm.py` | Club-тест переписан на `BackendRouter`; +2 теста: fallback в DeepSeek при недоступном local, local-only без ключа |
| `tests/fixtures/README.md` | Раздел про `eval_games.json`: инварианты, зачем, как менять |
| `docs/development/SPEC.md` | §3 (LLM клуба — local-first fallback) и §10 (приватность: fallback только при недоступном GPU) |
| `docs/development/reports/LLM_EVAL.md` | Пометка итерации 58: следующий прогон — на замороженном наборе; исторические межвыборочные таблицы сохранены |
| `TODO.md` | +T-124 (eval freeze), +T-125 (router), обновлён статус T-123 |
| `docs/CHANGELOG.md` | `[Unreleased] → Added`: frozen eval set + `BackendRouter` |
| `docs/development/RELEASES.md` | Раздел «Итерация 58» |
| `README.md` | Бейдж и счётчик backend-тестов, строка итерации |

---

## 2. Покрытие

| Область | Тесты | Что покрыто |
| :--- | :--- | :--- |
| Целостность frozen-набора | `test_committed_fixture_has_exactly_five_known_games`, `test_committed_fixture_pgns_are_legal_and_sha256_locked`, `test_eval_set_matches_previously_tested_players` | Ровно 5 игр, порядок/игроки, `game_id=-1..-5`, sha256 совпадает, PGN легален (`chess.pgn`, `errors=[]`), есть ходы |
| Загрузчик | 7 тестов (`returns_game_rows`, `respects_limit`, `default_limit_keeps_all`, `missing_file`, `rejects_tampered_pgn`, `rejects_unknown_schema`, `rejects_empty_games`, `rejects_invalid_pgn`) | `GameRow`-маппинг, limit, `FrozenEvalSetError` с понятными сообщениями, tamper-detection по sha256, отказ на schema≠1/пустом списке/битом PGN |
| CLI (офлайн) | 5 тестов | `--frozen-set` по умолчанию True, `--no-frozen-set`, `--frozen-file`; `main(["--source",...])` → exit 2 без БД; отсутствующий файл → exit 1 |
| Отчёт/JSON | 3 теста | Frozen-режим упоминает файл и same-sample и не содержит старой формулировки RAG; DB-режим сохранён; `"frozen": true` для отрицательных id |
| `is_unavailable_error` | 8 (7 параметров + cause chain) | ConnectError/ConnectTimeout/ReadTimeout/`LLMTimeoutError` → True; `LLMError` HTTP 4xx/«неожиданный ответ»/`ValueError` → False; обёрнутая причина (`raise ... from`) → True |
| `complete`-fallback | 6 тестов | local success не трогает fallback; ConnectError/ReadTimeout/обёрнутая ошибка → облако + лог `local_gpu_unavailable` (поля primary/fallback/error/cooldown); HTTP 400 без fallback; оба упали → ошибка резерва |
| Circuit-breaker | 3 теста | открывается после сбоя, пропускает primary в пределах cooldown (`local_gpu_unavailable` один раз), восстанавливается после cooldown (`llm_router_primary_recovered`), `cooldown=0` пробует всегда |
| Стрим | 2 теста | падение до первого чанка → fallback; падение после чанков → ошибка без дублирования |
| Служебное | 5 тестов | `set_level` пробрасывается во все бэкенды, `aclose` закрывает все, provider/model от primary, пустой список/отрицательный cooldown → `ValueError` |
| Factory | 3 теста | `club` → `BackendRouter` (2 бэкенда), fallback в DeepSeek через `MockTransport` (запрос ушёл в облако), без ключа — local-only |
| Регресс | существующие наборы | `test_lora_config.py`, `test_warmup.py`, `test_observability.py`, `test_llm_calls_api.py` и полная сюита |

Новые тесты: **45** (19 + 24 + 2). Полная сюита — **1356 passed**.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1356 passed, 15 skipped, 1 deselected, 1 xfailed** (208.12 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита (CI-подобно) | `pytest -q --deselect tests/integration/test_llm_live.py::test_live_gm_uses_deepseek` при живых PostgreSQL `5437`/Redis `6385` | **1356 passed, 15 skipped, 1 deselected, 1 xfailed in 208.12 s** (exit 0) |
| Eval-frozen | `pytest -q tests/unit/test_evaluate_llm_frozen.py` | **19 passed** |
| Router | `pytest -q tests/unit/test_llm_router.py` | **24 passed** |
| LLM/фабрика/LoRA/warmup/OTel | `pytest -q tests/unit/test_llm.py tests/unit/test_lora_config.py tests/unit/test_warmup.py tests/unit/test_observability.py` | **51 passed** |
| Router (+ те же) | `pytest -q tests/unit/test_llm_router.py` + 4 файла выше | **75 passed** |
| Unit-набор | `pytest -q tests/unit` | **1034 passed, 2 skipped, 1 xfailed** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 91 source files` |
| Миграции | `alembic upgrade head` + `alembic check` | `No new upgrade operations detected.` |
| CI | GitHub Actions после push | ✅ **success** — run [`42196291092`](https://github.com/iilmchat/ai-chess-annotator/actions/runs/42196291092): backend — **1360 passed, 12 skipped, 1 xfailed** (90.88 с), frontend / e2e / docker — success; billing-ошибка итераций 56–57 не воспроизводится |

**Skips (15):** 9 — live LM Studio/эмбеддинги (в среде нет GPU);
6 — `test_token_budget_api` с env-зависимыми портами локального окружения
(в CI-подобном окружении проходят; см. итерацию 57). **Deselected (1):**
live DeepSeek-тест — в `.env` есть ключ, но локальная сеть не выполняет
реальный вызов; в CI без ключа тест скипается.

---

## 4. Smoke

| Сценарий | Команда | Результат |
| :--- | :--- | :--- |
| Frozen — дефолт, guard `--source` | `python scripts/evaluate_llm.py --source new_samples --games 1` | exit **2**, stderr: «`--source` … несовместим с замороженным набором; добавьте `--no-frozen-set`» — без обращения к БД |
| Справка CLI | `python scripts/evaluate_llm.py --help` | Видны `--frozen-set/--no-frozen-set`, `--frozen-file`, `--source` с пометкой «только вместе с `--no-frozen-set`» |
| Файл отсутствует | `python scripts/evaluate_llm.py --frozen-file <missing> --games 1` | exit **1**, stderr «Ошибка замороженного eval-набора: … не найден» |
| Загрузчик | `_load_frozen_games(DEFAULT_FROZEN_SET)` | 5 `GameRow`, id `-1..-5`, `event="Moscow zt"`; порча PGN → `FrozenEvalSetError: PGN изменён (sha256 …)` |
| Router: local down → cloud | unit-тест с `MockTransport` | Запросы: `127.0.0.1:8034/…/chat/completions` (ConnectError) → `api.deepseek.com/chat/completions` (200), `usage.provider="deepseek"`; лог `local_gpu_unavailable` |
| Router: local up | unit-тест | fallback не вызывается (`fallback.calls == 0`), `provider/model` остаются локальными |
| Circuit-breaker | unit-тест с управляемыми часами | 2-й запрос в пределах cooldown не трогает primary; после +61 с primary пробуется снова |
| Инфраструктура тестов | `docker compose up -d postgres redis`, `alembic upgrade head`, `alembic check` | Контейнеры healthy; миграции applied; «No new upgrade operations detected» |

---

## 5. Что не так

- **Live-fallback не проверен на реальном железе.** В среде нет доступной
  LM Studio/GPU, поэтому переход «локальный → облачный» проверен
  mock-httpx и фейковыми бэкендами. Это осознанное ограничение: live-путь
  проверяется в T-123 (reverse SSH-туннель до RTX 3060), mock-тесты
  покрывают всю ветвящуюся логику.
- **Fallback только на недоступность.** HTTP 5xx/4xx локального бэкенда
  не переключают на DeepSeek: это ошибка ответа, а не доступности, и
  скрывать её fallback'ом нельзя. Если понадобится fallback на 5xx — это
  отдельное решение (см. T-123).
- **Стрим переключается только до первого чанка.** После начала потока
  повтор невозможен без дублирования текста — ошибка пробрасывается
  потребителю.
- **Club-партия может уйти в облако при недоступном GPU.** Это явное
  продуктовое решение из постановки (fallback), зафиксировано в
  `SPEC.md` §10; `beginner` остаётся строго локальным, `gm` — облачным.
- **Полный v4-eval не выполнен:** обучение v4 на 556 записях отложено
  (нужен GPU). Итерация проверяет инфраструктуру воспроизводимости, а не
  качество модели; первые same-sample дельты появятся после v4.
- **15 skip в локальном прогоне** (9 live LM Studio, 6 env-зависимых
  `token_budget`) — известное ограничение среды, не связанное с
  изменениями; в CI-окружении `token_budget` проходит.
- **`test_live_gm_uses_deepseek` deselected локально:** в `.env` есть
  ключ DeepSeek, но среда не выполняет реальный сетевой вызов; в CI без
  ключа тест скипается.

---

## 6. Новые KI

**Нет.** Ограничения выше либо следуют из постановки (fallback/приватность,
стрим), либо являются известными ограничениями среды (live-тесты, GPU) и
закрываются существующими задачами T-123/T-104b. Отдельный KI не нужен:
поведение задокументировано, тесты детерминированы.

---

## 7. Обоснование

- **Почему JSON-фикстура, а не «фиксированный `source` в БД».** Набор
  должен быть доступен без PostgreSQL/ingest и коммититься вместе с кодом;
  sha256 каждого PGN превращает «случайное изменение» в явную ошибку
  `FrozenEvalSetError`. Отрицательные `game_id` отделяют синтетические id
  от строк `annotated_games`, не меняя тип `GameRow`.
- **Почему frozen — поведение по умолчанию.** Итерация 52 показала:
  сравнение версий ломается, как только набор меняется. Дефолт должен быть
  воспроизводимым; случайная выборка из RAG остаётся явным
  `--no-frozen-set` для исследовательских прогонов.
- **Почему `BackendRouter` — наследник `BaseLLMClient`, а не «if» в
  сервисах.** Комментарий, чат и стриминг уже работают с абстракцией
  `BaseLLMClient`; роутер не требует правок сервисов, пробрасывает
  `set_level` (метрики `llm_calls`/спаны сохраняют уровень) и закрывает
  оба HTTP-клиента. `provider`/`model` остаются локальными — внешний код и
  healthcheck видят прежнего провайдера.
- **Почему короткий таймаут и 0 ретраев у локального бэкенда.** «Мгновенный
  fallback» возможен, только если локальный вызов падает быстро: 2 с вместо
  120 с production-таймаута, а ретраи — это и есть переход в облако.
  Circuit-breaker (60 с) убирает 2-секундную задержку на каждом запросе,
  пока GPU мёртв, и автоматически восстанавливается.
- **Почему fallback узкий (только соединение/таймаут).** HTTP 4xx/«кривой
  ответ» — это дефект модели/промпта, а не доступности; fallback на облако
  маскировал бы регрессии и менял бы семантику ошибок. Обёрнутые клиентом
  `LLMError` распознаются по цепочке `__cause__`.
- **Почему `beginner` не получил fallback.** RULES §7.3/SPEC §10: партии
  новичков не уходят в облако. Роутер включается только для `club`
  (осознанное продуктовое решение), поэтому приватность `beginner`
  гарантирована на уровне фабрики.
- **Почему прогрев переведён на `beginner`.** После включения fallback'
  прогрев `club`-клиентом при выключенном GPU тратил бы облачные токены на
  служебный запрос. `beginner` — прямой локальный клиент, что и нужно
  прогреву.
- **Почему env-конфиг, а не константы.** T-123 требует настраивать URL
  туннеля/таймауты/cooldown через окружение; новые поля `Settings` следуют
  общему стилю проекта (никаких `os.environ` напрямую) и не добавляют
  зависимостей.
