# Итерация 60 — Fix Timeout Defaults & V4 Prep (T-127, T-104b)

**Дата:** 2026-11-06
**Статус:** ✅ завершена — дефолт `LLM_LOCAL_TIMEOUT_S` поднят с 2 до
**120 с** (иначе живой GPU считался недоступным и `club` всегда уходил в
DeepSeek), в `evaluate_llm.py` добавлен `--model-name` для same-sample
сравнения v3/v4; `pytest`/`ruff`/`mypy` зелёные.
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` = `5af96ef` (итерация 59: настройки
GPU-туннеля в `Settings`, гайд `DEPLOYMENT_GPU_TUNNEL.md`, CI зелёный).
В итерации 59 выяснилось, что `LLM_LOCAL_TIMEOUT_S=2.0` — это таймаут
HTTP-запроса локального клиента, а не «пинг»: генерация 7B занимает
10–15 с, поэтому с 2 с роутер считал живой GPU недоступным и **каждый**
клубный комментарий уходил в DeepSeek (local-first фактически не работал).
Параллельно шла подготовка к v4: обучение на 556 записях ждёт GPU, а eval
v3/v4 должен идти на одном замороженном наборе, поэтому отчёту и JSON нужна
явная метка версии модели.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `tests/unit/test_evaluate_llm_model_name.py` | 10 офлайн-тестов `--model-name`: дефолт, override, заголовок отчёта, `model_name` в JSON, пустая метка → exit 2 |
| `docs/development/reports/v1.5.0/ITERATION-60.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/config.py` | `llm_local_timeout_s`: **2.0 → 120.0** + docstring: таймаут — время HTTP-запроса, должен покрывать генерацию 7B; отказ соединения и так даёт мгновенный `ConnectError`, таймаут — только для «чёрной дыры»; `llm_router_cooldown_s` оставлен 60.0 |
| `.env.example` | `LLM_LOCAL_TIMEOUT_S=120` + переписанный комментарий (покрыть генерацию, не «пинг») |
| `tests/unit/test_llm.py` | Тест дефолтов: `llm_local_timeout_s == 120.0` (было 2.0) |
| `scripts/evaluate_llm.py` | `DEFAULT_MODEL_NAME = "qwen-7b-tokenizer_gguf-v4"`; CLI `--model-name`; заголовок отчёта `> **Модель:** …`; `_dump_json(..., model_name=…)` пишет `model_name` в каждую запись рядом с API-именем `model`; пустая метка → exit 2; обновлён docstring |
| `docs/development/DEPLOYMENT_GPU_TUNNEL.md` | §7 (`.env`, таблица), §8.2 (таймауты переписаны под 120 с), чек-лист/§6, troubleshooting (2 строки), rollback — убраны утверждения про «2 с fail-fast» |
| `docs/CHANGELOG.md` | `[Unreleased] → Fixed`: дефолт 120 с и `--model-name` |
| `docs/development/RELEASES.md` | Раздел «Итерация 60» |
| `docs/development/reports/v1.5.0/ITERATION-59.md` | Поправка: дефолт 2 с из итерации 59 заменён на 120 с в 60 |
| `TODO.md` | +T-127 (**[x]**), актуализированы T-125/T-126 |
| `README.md` | Бейдж/счётчик backend-тестов, строка итерации 60 |

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| Дефолт таймаута | `test_local_llm_base_url_defaults_to_lmstudio` (обновлён) | `llm_local_timeout_s == 120.0`, `llm_router_cooldown_s == 60.0`, URL LM Studio |
| Env-override | `test_tunnel_settings_are_read_from_env` | `LLM_LOCAL_TIMEOUT_S=3.5` из окружения по-прежнему переопределяет дефолт |
| Factory | `test_factory_club_uses_tunnel_settings` | Явный `llm_local_timeout_s=3.5` доходит до `router.primary.timeout_s` (дефолт не зашит в фабрику) |
| `--model-name` дефолт | `test_default_model_name_is_v4`, `test_parse_args_uses_default_model_name` | Константа и argparse по умолчанию — `qwen-7b-tokenizer_gguf-v4` |
| `--model-name` override | `test_parse_args_accepts_model_name_override` | `--model-name qwen-7b-tokenizer_gguf-v3` парсится |
| Отчёт | `test_report_contains_model_name_in_header`, `test_report_without_model_name_stays_backward_compatible`, `test_report_distinguishes_v3_and_v4` | Метка в заголовке; без флага отчёт не меняется; v3/v4 различаются |
| JSON | `test_dump_json_keeps_api_model_and_adds_model_name`, `test_dump_json_without_model_name_is_null`, `test_dump_json_writes_model_name_for_every_row` | `model` (API) и `model_name` (метка) сосуществуют; `null` без флага; метка в каждой записи |
| Валидация CLI | `test_main_rejects_blank_model_name_offline` | Пустая метка → exit 2 до БД/LLM |
| Регресс | `test_llm_router.py` (24), `test_evaluate_llm_frozen.py` (19), `test_evaluate_llm_cjk.py` и др. | Роутер и frozen-набор не изменились, тесты не правились |

Новые тесты: **10** (`tests/unit/test_evaluate_llm_model_name.py`).

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1372 passed, 15 skipped, 1 deselected, 1 xfailed** (170.51 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Полная сюита (CI-подобно) | `pytest -q --deselect tests/integration/test_llm_live.py::test_live_gm_uses_deepseek` при живых PostgreSQL `5437`/Redis `6385` | **1372 passed, 15 skipped, 1 deselected, 1 xfailed in 170.51 s** (exit 0) |
| Eval-набор тестов | `pytest -q tests/unit/test_evaluate_llm_model_name.py tests/unit/test_evaluate_llm_frozen.py tests/unit/test_evaluate_llm_cjk.py tests/unit/test_evaluate_llm_explanations.py tests/unit/test_evaluate_llm_analyze_errors.py` | **57 passed** |
| LLM/роутер/LoRA/warmup | `pytest -q tests/unit/test_llm.py tests/unit/test_llm_router.py tests/unit/test_lora_config.py tests/unit/test_warmup.py` | **75 passed** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app scripts/evaluate_llm.py` | `Success: no issues found in 92 source files` |
| Миграции | `alembic check` | `No new upgrade operations detected.` |
| CI | GitHub Actions после push | (заполняется после прогона) |

**Skips (15):** 9 — live LM Studio/эмбеддинги (нет GPU в среде); 6 —
`test_token_budget_api` с env-зависимыми портами (в CI проходят).
**Deselected (1):** live DeepSeek-тест (в CI скипается без ключа).

---

## 4. Smoke

| Сценарий | Команда | Результат |
| :--- | :--- | :--- |
| Дефолт таймаута | `python -c "from app.config import Settings; print(Settings(_env_file=None).llm_local_timeout_s)"` | `120.0` |
| Дефолт в `.env.example` | `grep LLM_LOCAL_TIMEOUT_S .env.example` | `LLM_LOCAL_TIMEOUT_S=120`, `LLM_ROUTER_COOLDOWN_S=60` |
| Роутер с дефолтом | `get_llm_client("club", Settings(_env_file=None, deepseek_api_key="x"))` | `router.primary.timeout_s == 120.0`, `cooldown_s == 60.0` |
| Override таймаута | `Settings(..., llm_local_timeout_s=30)` → `get_llm_client("club", ...)` | `router.primary.timeout_s == 30.0` |
| `--model-name` в отчёте | `_report(..., model_name="qwen-7b-tokenizer_gguf-v3")` | Заголовок содержит `> **Модель:** \`qwen-7b-tokenizer_gguf-v3\`` |
| `--model-name` в JSON | `_dump_json([row], path, model_name="qwen-7b-tokenizer_gguf-v4")` | Каждая запись: `"model": "<API-имя>"`, `"model_name": "qwen-7b-tokenizer_gguf-v4"` |
| CLI-справка | `python scripts/evaluate_llm.py --help` | Виден `--model-name` с дефолтом `qwen-7b-tokenizer_gguf-v4` |
| Пустая метка | `main(["--model-name","  ","--games","1"])` | exit **2**, stderr «`--model-name` не может быть пустым», без БД/LLM |
| Live-прогон | — | Не выполнялся: нет LM Studio/GPU в среде; параметры покрыты тестами |

---

## 5. Что не так

- **Семантика 120 с как «чёрная дыра».** При выключенном ПК listener
  исчезает → `ConnectError` мгновенный, и 120 с не ждём. Но если пакеты до
  Windows-машины уходят в никуда (sshd принял, target не отвечает), первый
  запрос подождёт до 120 с, затем circuit-breaker (60 с) пропускает local.
  Это осознанный размен: нельзя одновременно иметь длинный таймаут для
  генерации и мгновенный для недоступности на одном HTTP-клиенте. Если
  понадобится — отдельный connect-timeout (например, 5 с) и read-timeout
  120 с; это отдельная задача.
- **`LLM_LOCAL_TIMEOUT_S=120` не проверен live на 3060.** В среде нет GPU;
  значение выбрано по замерам из отчётов (генерация 7B — 10–15 с) и
  совпадает с `LLM_TIMEOUT_S`. На стенде стоит подтвердить `latency_ms`.
- **Метка `--model-name` — не имя модели для запроса.** Она не меняет
  `VLLM_MODEL`/`LORA_ADAPTER_PATH`: это ярлык для отчёта/JSON, чтобы v3 и
  v4 на одном frozen set не путались. `model` в JSON по-прежнему приходит
  от API (может быть базовым именем вместо имени адаптера).
- **Исторические документы.** В `ITERATION-59.md` и разделах RELEASES
  итерации 59 остались описания дефолта 2 с как состояния на тот момент;
  в отчёт 59 добавлена явная поправка, гайд и CHANGELOG обновлены — двух
  «истин» о текущем дефолте нет.
- **v4-прогон не выполнен** — обучение на 556 записях ждёт GPU; итерация
  готовит инструмент (frozen set + метка версии), а не результаты.

---

## 6. Новые KI

**Нет.** Проблема «2 с ломает local-first» была не багом кода, а неверным
дефолтом — исправлена и покрыта тестом; остальные ограничения (нет live
GPU, «чёрная дыра» ждёт таймаут) известны и задокументированы. Отдельный
реестровый KI не требуется.

---

## 7. Обоснование

- **Почему 120 с, а не 90 с.** Требование — покрыть генерацию 7B
  (10–15 с) с запасом на длинные пакеты фактов и медленный туннель;
  120 с совпадает с уже принятым `LLM_TIMEOUT_S` и не вводит второй
  «магический» таймаут. Верхняя граница всё равно ограничена
  `LLM_TIMEOUT_S`/`--timeout-s` eval-скрипта.
- **Почему не «пинг перед генерацией».** Отдельный health-probe перед
  каждым запросом удвоил бы задержку и всё равно гонялся бы с состоянием
  туннеля; короткий HTTP-таймаут оказался хуже, потому что не различал
  «медленно генерирует» и «недоступен». Circuit-breaker уже решает вторую
  часть: после сбоя local пропускается 60 с.
- **Почему `--model-name` — отдельный ярлык, а не подмена `VLLM_MODEL`.**
  Модель для запроса определяется окружением (vLLM/LM Studio), а отчёт
  должен быть воспроизводимым и сравнимым: v3 и v4 гоняются одной командой
  на одном frozen set, и версия должна быть видна в артефактах. Ярлык
  хранится рядом с API-именем `model`, поэтому видно и фактический ответ
  сервера, и заявленную версию.
- **Почему пустая метка — ошибка, а не дефолт.** Тихий откат к дефолту
  смешал бы прогоны v3/v4 в одном JSON; лучше упасть до запуска.
- **Почему обновлён гайд, а не только код.** `DEPLOYMENT_GPU_TUNNEL.md` —
  операционная инструкция: если бы он продолжал утверждать «2 с fail-fast»,
  деплой на VPS воспроизвёл бы исходную проблему. Документация — часть
  исправления (RULES §5.1: один источник истины).
