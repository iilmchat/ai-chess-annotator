# Итерация 63 — Housekeeping & Docs (T-130/KI-044)

**Дата:** 2026-11-08
**Статус:** ✅ завершена — N+1 `load_analyses` при экспорте датасета закрыт
кэшем по `game_id` (KI-044), README получил раздел «Метрики качества»;
`pytest`/`ruff`/`mypy` зелёные.
**Формат:** RULES §2.18 (7 разделов).

**Исходное состояние.** `main` после итерации 62: baseline v3 (**3.60/10**)
и 556 записей v4-датасета существовали, но были размазаны по
`LLM_EVAL.md`/`RELEASES.md`, а KI-044 оставался Open: экспорт v2 вызывал
`load_analyses` на каждую правку, поэтому 3 правки одной партии читали
`moves`/`motifs` трижды. Обучение v4 идёт на AMD GPU, поэтому итерация
посвящена техническому долгу без LLM/GPU.

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `tests/unit/test_dataset_export_cache.py` (187 строк) | **4** офлайн-теста: 3 правки одной партии → 1 `load_analyses` (и для `iter_export_records`, и для `scripts/export_dataset.py`), 2 партии → 2 вызова, партия без анализа → 1 вызов и 0 записей |
| `docs/development/reports/v1.5.0/ITERATION-63.md` | Этот отчёт (7 разделов) |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/dataset/records.py` | `AnalysesCache` (dict `game_id → list[MoveAnalysis]`, счётчики hits/misses); `build_export_record(..., analyses_cache=None)`; `iter_export_records` создаёт один кэш на выгрузку; docstring |
| `scripts/export_dataset.py` | `_export_v2` создаёт `AnalysesCache` на весь экспорт и передаёт в `_record_v2_or_skip`; прямой импорт `load_analyses` убран; docstring |
| `README.md` | Новый раздел **«Метрики качества»** сразу после интро: baseline v3 3.60/10, разбивка 2.60/3.40/4.80, объяснения 60%, CJK 0.00%, v4-датасет 556 записей, порог 7/10, команда сравнения; обновлены счётчики тестов/итерация/KI-реестр |
| `TODO.md` | +T-130 (**[x]**) — кэш `load_analyses` |
| `docs/KNOWN_ISSUES.md` | KI-044 **Closed** (решение, доказательство, регресс); итог реестра — 61 запись, 25 Open / 36 Closed |
| `docs/CHANGELOG.md` | `[Unreleased] → Fixed`: N+1 `load_analyses` при экспорте |
| `docs/development/RELEASES.md` | Раздел «Итерация 63» + метрики |
| `docs/README.md` | Индекс отчётов `v1.5.0` расширен до итерации 63 |

---

## 2. Покрытие

| Область | Тест | Что покрыто |
| :--- | :--- | :--- |
| Админка/фон | `test_three_edits_of_one_game_call_load_analyses_once` | 3 правки одной партии через `iter_export_records` → 1 вызов `load_analyses`, 3 записи, 2 `execute` (батч + терминальный пустой) |
| CLI-скрипт | `test_export_v2_three_edits_of_one_game_call_load_analyses_once` | Тот же сценарий для `_export_v2` из `scripts/export_dataset.py` (путь KI-044): 3 JSONL-строки, 1 вызов |
| Ключ кэша | `test_cache_is_keyed_by_game_id` | 2 партии × 2 правки → ровно 2 вызова, кэш не «склеивает» разные `game_id` |
| Пустой анализ | `test_empty_analysis_is_cached_for_all_edits` | `load_analyses` вернул `[]` → 1 вызов на 3 правки, записей 0 (повторно в БД не ходим) |
| Регресс | `tests/unit/test_dataset_export.py`, `tests/integration/test_dataset_export_v2.py`, `tests/integration/test_admin_exports.py`, `tests/integration/test_export_jobs.py`, `tests/unit/test_generate_lora_dataset.py` | JSONL v1/v2, пропуски `no_game`/`invalid_pgn`/`no_analysis`, статистика, streaming/фоновые джобы — без изменений |

Новые тесты: **4**. Все — офлайн, без БД: `load_analyses` и
`build_fact_sheet` подменены, строки `Edit`/`Commentary`/`Game`
сконструированы в памяти.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений (Python, сборка не требуется).
**Tests:** **1408 passed, 17 skipped, 1 deselected, 1 xfailed** (133.56 с,
exit 0).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Новый файл тестов | `pytest -q tests/unit/test_dataset_export_cache.py` | **4 passed** (1.04 с) |
| Экспортные тесты с БД | `pytest -q tests/unit/test_dataset_export_cache.py tests/integration/test_dataset_export_v2.py tests/integration/test_admin_exports.py tests/integration/test_export_jobs.py` | **33 passed** (15.05 с) |
| Полная сюита | `pytest -q --deselect tests/integration/test_llm_live.py::test_live_gm_uses_deepseek` (PostgreSQL `5437`, Redis `6385`, реальный Stockfish) | **1408 passed, 17 skipped, 1 deselected, 1 xfailed in 133.56 s** (exit 0) |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app scripts/export_dataset.py` | `Success: no issues found in 92 source files` |

**Skip (17):** 8 live LM Studio (chat/commentary/4 embeddings/generation/
llm_live) + 7 env-зависимых `token_budget` + 2 теста без `libcairo2`.
**Deselected (1):** live DeepSeek. **Xfailed (1):** маркер KI-012.

Относительно итерации 62 passed меньше (1408 против 1412) не из-за
регресса: в этом прогоне LM Studio была недоступна, поэтому 8 live-тестов
ушли в skip; плюс **+4** новых теста.

---

## 4. Smoke

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Счётчик вызовов | `pytest -q tests/unit/test_dataset_export_cache.py -vv` | 4 passed; в тестах утверждается `calls == [42]` для 3 правок одной партии и `calls == [1, 2]` для двух партий |
| JSONL на выходе | тот же тест `_export_v2` | 3 строки JSONL, `stats.exported == 3`, `load_analyses` — 1 вызов |
| Реальный экспорт | `pytest -q tests/integration/test_dataset_export_v2.py tests/integration/test_admin_exports.py tests/integration/test_export_jobs.py` | 33 passed: v2-датасет, streaming `edits.jsonl`, фоновые `export_jobs` на реальной БД |
| Импорт/типы | `mypy app scripts/export_dataset.py` | 0 ошибок: `AnalysesCache` типизирован, циклических импортов нет |

Фактический вывод нового файла:

```text
....                                                                     [100%]
4 passed in 1.04s
```

Кэш не меняет содержимое JSONL: тест `_export_v2` проверяет и число
строк, и `stats.exported`; интеграционные тесты сравнивают записи
v1/v2 и пропуски — расхождений нет.

---

## 5. Что не так

- **Кэш не ограничен по размеру.** Dict живёт всю выгрузку и хранит
  анализы всех встреченных партий. Для тысяч уникальных партий это память
  на `MoveAnalysis`; правки одной партии обычно идут подряд, поэтому
  локальность высокая. Если понадобится — заменить dict на bounded LRU
  (`OrderedDict`, 128–256 партий), не меняя публичный интерфейс
  `AnalysesCache.get`.
- **Кэш не инвалидируется.** Это осознанно: выгрузка — снимок на время
  процесса, данные партии не меняются. Если во время долгого экспорта
  кто-то перезапишет `moves`/`motifs`, запись может быть собрана по
  старому анализу; для batch-экспорта это приемлемо и задокументировано
  в docstring.
- **Бенчмарк на 1000 правок не добавлен.** План KI-044 упоминал
  бенчмарк, но wall-clock тест на Windows нестабилен; закрытие
  подтверждено счётчиком вызовов (детерминированно), а не временем.
  Это отражено в KI-044.
- **`build_export_record` без кэша по-прежнему делает запрос на каждый
  вызов.** Так сохранена совместимость одиночных вызовов и тестов; все
  production-пути (`iter_export_records`, `_export_v2`) передают кэш.
- **Full-suite skip вырос до 17** из-за недоступной LM Studio в этом
  прогоне; это окружение, а не регресс (в CI live-тесты также skip).
- **v4-eval не выполнен:** модель ещё обучается; README фиксирует
  baseline и датасет, но не выдаёт v4 за готовый результат.
- **GitHub Actions не запускался до push**; локальный полный прогон
  зелёный, CI проверит коммит после push.

---

## 6. Новые KI

**Нет.** KI-044 закрыт. Ограничения из §5 (неограниченный размер кэша,
отсутствие инвалидации, нет бенчмарка) — осознанные компромиссы в рамках
одной выгрузки; при переходе на постоянный сервис экспорта их стоит
пересмотреть, но отдельный KI сейчас не нужен.

---

## 7. Обоснование

- **Почему кэш в `app/dataset/records.py`, а не в скрипте.** Оба
  production-пути (`iter_export_records` для streaming/фоновых джоб и
  `_export_v2` для CLI) страдают от одного N+1. Общий `AnalysesCache`
  убирает дублирование и гарантирует одинаковое поведение; скрипт лишь
  создаёт кэш и передаёт его.
- **Почему кэш на одну выгрузку, а не глобальный `lru_cache`.** Глобальный
  кэш пережил бы запросы и стал бы источником устаревших данных и утечки
  памяти. Время жизни «один вызов `iter_export_records`/`_export_v2`»
  совпадает со снимком данных, для которого дельты допустимы.
- **Почему ключ — только `game_id`.** Анализ (`moves`/`motifs`) принадлежит
  партии и не зависит от правки или уровня; `parsed` строится из того же
  `game.pgn`. Кэшировать `parsed` отдельно не нужно — парсинг PGN дёшев по
  сравнению с двумя SQL-запросами, а задача была именно про N+1 в БД.
- **Почему пустой список тоже кэшируется.** Иначе партия без
  `/v1/analyze` с 50 правками делала бы 100 запросов, чтобы 50 раз
  пропустить записи. `cached is not None` отличает «нет анализа» от
  «ещё не спрашивали».
- **Почему параметр `analyses_cache` опциональный.** `build_export_record`
  — публичная функция, её вызывают тесты и возможные внешние сценарии;
  `None` сохраняет прежнее поведение без кэша и не ломает контракт.
- **Почему README-раздел, а не только строка в статусе.** Baseline v3 и
  объём v4-датасета — главные цифры для решения «катить v4 или нет».
  Раздел сразу после интро виден без прокрутки, а статусная таблица
  ссылается на него; детали остаются в `LLM_EVAL.md` (один факт — один
  источник, RULES §5.2).
- **Почему коммит `perf(dataset)`.** Изменение уменьшает число SQL-запросов
  без изменения поведения/схемы JSONL; Conventional Commits — тип `perf`,
  scope `dataset`. README-метрики в том же коммите — документация к
  изменению и подготовка к v4-eval.
