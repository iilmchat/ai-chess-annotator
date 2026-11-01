# Итерация 45 — Lichess Golden Dataset Builder (T-092, T-093)

**Дата:** 2026-11-01
**Статус:** ✅ завершена локально — тесты с моками API/LLM, линтеры и
типы зелёные; реальные Lichess-загрузка и локальная LLM — на машине с
сетью/моделью, полная backend-сюита — CI.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `scripts/fetch_lichess_games.py` (469 строк) | Lichess API: PGN по нику или ID, `evals=true`, фильтр рейтинга 1800–2200, standard-вариант, наличие `[%eval]`, запись `data/gold_pgns/*.pgn` по 50–100 партий |
| `scripts/build_gold_dataset.py` (610 строк) | Парсинг `[%eval …]`, `MoveAnalysis` без выдуманного лучшего хода, `build_fact_package`, mock/local LLM, валидация через `app/engine/validator.py`, JSONL `schema_version=2` |
| `tests/unit/test_fetch_lichess_games.py` (286 строк) | 16 тестов: MockTransport, URL/параметры, POST-ID, фильтры, чанки, CLI |
| `tests/unit/test_build_gold_dataset.py` (419 строк) | 27 тестов: `[%eval]`/мат, `MoveAnalysis`, v2-контракт, skip no-evals/invalid-pgn/invalid-llm, retry, mock/local, CLI |
| `docs/development/reports/v1.5.0/ITERATION-45.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/engine/models.py` | `MoveAnalysis.best_move_known: bool = True`; `False` — из PGN известна только оценка, лучший ход не восстановить |
| `app/llm/prompts.py` | Для `best_move_known=False` пакет печатает «Потеря относительно лучшей линии» и «Лучший ход движка неизвестен» вместо выдуманного хода |
| `tests/unit/test_prompts.py` | +1 тест: пакет без лучшего хода не выдаёт `best_move_san` за лучший ход |
| `docs/development/GPU_TRAINING_GUIDE.md` (1128 строк) | §11 «Бесплатные GPU: Kaggle и Colab»: T4, 4B, датасет/Drive, команды `train_lora.py`, TensorBoard, resume, ограничения; TOC и чек-лист |
| `TODO.md` | T-092 и T-093 отмечены `[x]` |
| `docs/CHANGELOG.md` | `[Unreleased]` → `Added`: Lichess builder и бесплатные GPU |
| `docs/development/RELEASES.md` | Запись об итерации 45 |
| `.gitignore` | `data/gold_pgns/`, `data/gold_dataset.jsonl` |

### Рабочее дерево

В дереве также есть незакоммиченные изменения прошлых итераций (T-087…T-091)
и параллельной работы (`README.md`, `docs/development/GETTING_STARTED.md`,
`scripts/install_stockfish.py`, `tests/unit/test_install_stockfish.py`,
`tests/integration/test_auth_api.py`, `docker-compose.yml`, `chess_code.tar.gz`).
Они не затрагивались.

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| Lichess API | `test_fetch_lichess_games.py` | GET `/api/games/user/{username}`; `evals=true`, `rated=true`, `max`, `perfType`; POST `/api/games/export/_ids` с CSV-телом; пустой ответ/отсутствие селектора → `FetchError` |
| Фильтрация | `test_fetch_lichess_games.py` | Рейтинг обоих игроков 1800–2200; отсутствующий Elo; не-standard вариант; отсутствие `[%eval]`; кастомный диапазон |
| Файлы | `test_fetch_lichess_games.py` | chunks по 2 игры → 3 файла; имена `prefix_001.pgn`; roundtrip через `parse_games`; `per_file` 0/101 → ошибка |
| CLI загрузчика | `test_fetch_lichess_games.py` | `--username`/`--game-ids`; HTTP 404 → exit 2; пустая выборка → exit 2; defaults |
| `[%eval]`-парсер | `test_build_gold_dataset.py` | `0.23`, `-1.09`, `+0.00`, `#3`, `#-3`; мусор/`#0` → `None` |
| `MoveAnalysis` | `test_build_gold_dataset.py` | Одна оценка на полуход; White POV → сторона хода; delta 160 → `mistake`; `best_move_known=False`; мат `mate_after` |
| v2-запись | `test_build_gold_dataset.py` | `schema_version=2`, роли system→user→assistant, metadata (source/site/Elo/eval_points); `train_lora.validate_dataset` принимает файл |
| Пропуски/retry | `test_build_gold_dataset.py` | `no_evals`, `invalid_pgn`, `invalid_llm`; локальная LLM с нелегальным ходом → retry и успех; все попытки нелегальны → skip |
| Сборка/CLI | `test_build_gold_dataset.py` | Mock end-to-end, лимит, отсутствие каталога/файлов; defaults, неизвестный provider, `main` exit 0/2 |
| Пакет без best move | `test_prompts.py` | `best_move_known=False` → «Лучший ход движка неизвестен», нет ложного `best_move_san` |
| Регрессия | связанный набор | **248 passed**: fetch, build, prompts, train, export GGUF, generator, dataset export, внешний конвертер |

Новых тестов: **+44** (16 fetch + 27 build + 1 prompt). Frontend не менялся.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Тесты загрузчика | `pytest tests/unit/test_fetch_lichess_games.py -q` | **16 passed in 0.13 s** |
| Тесты сборщика | `pytest tests/unit/test_build_gold_dataset.py -q` | **27 passed in 0.91 s** |
| Тесты промптов | `pytest tests/unit/test_prompts.py -q` | **33 passed in 7.05 s** |
| Связанный набор | fetch + build + prompts + train + export + generator + dataset_export + convert | **248 passed in 30.15 s** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Типы новых файлов | `mypy scripts/fetch_lichess_games.py scripts/build_gold_dataset.py tests/unit/test_fetch_lichess_games.py tests/unit/test_build_gold_dataset.py` | `Success: no issues found in 4 source files` |
| Полная backend-сюита | `pytest -q` | локально не завершена (нет Stockfish в `PATH`, нестандартный PostgreSQL); проверка — CI |

**Smoke сборщика (`--provider mock`):**

```text
Золотой датасет: schema_version=2
PGN-файлов: 1
Партий: 1
Записей: 1
Пропущено: 0
Средняя длина assistant: 291 символов
Файл: .../gold.jsonl
schema 2 roles ['system', 'user', 'assistant']
has_eval_line True
assistant_len 291
```

В пакете фактов: «Потеря относительно лучшей линии: 160 cp» и «Лучший ход
движка неизвестен: в PGN есть только оценка».

**CLI:**

```text
fetch_lichess_games.py (--username USERNAME | --game-ids GAME_IDS)
  [--output-dir] [--max-games] [--per-file] [--min-elo] [--max-elo]

build_gold_dataset.py [--input-dir] [--output] [--limit]
  [--level {beginner,club,gm}] [--provider {local,mock}] [--max-retries]
```

---

## 4. Smoke

1. **Lichess API (mock).** `httpx.MockTransport` фиксирует GET-параметры
   (`evals=true`, `rated=true`, `max=50`, `perfType=blitz,rapid,classical`)
   и POST `/api/games/export/_ids` с телом `id1,id2`; ни одного реального
   сетевого вызова в тестах.
2. **Фильтр.** Из 5 партий выбрана одна: 1900/2000 + `[%eval]`; отброшены
   1700, отсутствие Elo, Chess960 и PGN без eval.
3. **Сборка.** PGN с `1. e4 { [%eval 0.20] } … 2. Nf3 { [%eval -1.50] }`
   → 1 запись, `schema_version=2`, ключевой момент `Nf3` с потерей 160 cp,
   `best_move_known=False`, mock-ответ 291 символ.
4. **Валидация.** `train_lora.validate_dataset` принимает выходной JSONL.
5. **Реальные Lichess/LLM не вызывались:** в локальной среде нет
   гарантированного доступа к Lichess/локальной LLM; контракты покрыты
   моками, реальный прогон — на машине с сетью/моделью.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| Реальный Lichess API не вызван | Тесты используют MockTransport; не проверены rate limit, 429 и актуальные PGN-ответы Lichess |
| Реальная локальная LLM не вызвана | В тестах — fake-клиент; качество русского текста и поведение при retry на реальной модели не проверены |
| Оценки Lichess — не наш Stockfish | `[%eval]` зависит от глубины/версии Lichess; для фактов это чужие, но валидные оценки, не воспроизводимые локально |
| Нет лучшего хода | `best_move_known=False`: пакет честно говорит «неизвестен»; LLM не может назвать альтернативу, кроме сыгранного хода |
| Mock-ответ не фактологичен | `MOCK_RESPONSE` — фиксированный русский текст для pipeline/CI, не привязан к конкретной партии |
| Часть партий без evals | Lichess отдаёт `[%eval]` только для проанализированных партий; такие PGN отбрасываются (`no_evals`) |
| Путь валидатора в постановке | В задаче указан `app/commentary/validator.py`, но в проекте валидатор лежит в `app/engine/validator.py`; использован фактический модуль (`validate_llm_response`) |
| Дедупликация не делается | Одинаковые партии/ID могут попасть дважды; лимит и фильтр не удаляют дубли |
| Сеть/лимиты Lichess | Один запрос; при 429 скрипт вернёт exit 2 с ошибкой, автоматического ожидания нет |
| Бесплатные GPU не проверены вживую | Kaggle/Colab-инструкция не исполнялась; лимиты и версии CUDA могут отличаться от описанных |
| Чужие незакоммиченные изменения | README/GETTING_STARTED/install_stockfish/auth-тест/архив изменены параллельно; не затрагивались |

---

## 6. Новые KI

**Нет.** Новых `KI-NNN` не заводилось: новые скрипты покрыты unit-тестами
с моками, линтеры/типы зелёные, отсутствие лучшего хода обработано явно
(`best_move_known=False`), а не выдачей сыгранного хода за лучший.
Реальные сеть/LLM/GPU — проверка окружения, не дефект кода. KI-030
(качество LoRA/LLM) остаётся открытым.

---

## 7. Обоснование

- **Почему Lichess `[%eval]`, а не повторный Stockfish.** Lichess уже
  хранит computer analysis; повторный анализ 1000 партий стоил бы часы
  GPU. Оценки пересчитываются из White POV в сторону хода — тот же
  контракт `MoveAnalysis`, что у движка.
- **Почему `best_move_known=False`.** В PGN с `evals=true` есть только
  оценка, а не лучший ход. Подставлять сыгранный ход в `best_move_san`
  значило бы врать в пакете фактов; отдельный флаг и честная строка
  «лучший ход неизвестен» сохраняют фактологию.
- **Почему фильтр рейтинга локальный.** У Lichess API нет
  `minRating`/`maxRating` для экспорта; `WhiteElo`/`BlackElo` из заголовков
  — единственный надёжный способ отобрать клубный уровень.
- **Почему `evals=true` и проверка `[%eval]`.** Без оценки пакет фактов
  теряет ключевые моменты; такие партии отбрасываются, чтобы не тратить
  генерацию LLM впустую.
- **Почему mock-провайдер.** CI/локальная машина без LM Studio должны
  проверять весь pipeline; mock-текст без SAN проходит валидатор, но
  помечен `model="mock"`, чтобы не смешивать с реальными данными.
- **Почему retry и валидатор.** Локальная LLM может придумать ход;
  `validate_llm_response` ловит нелегальные SAN, retry просит исправить,
  после исчерпания запись пропускается (`invalid_llm`), а не попадает в
  обучение.
- **Почему 50–100 партий на файл.** Удобно открыть/проверить вручную и
  не держать один огромный PGN; `--per-file` ограничен 1–100, дефолт 50.
- **Почему `data/gold_pgns/` и `data/gold_dataset.jsonl` в `.gitignore`.**
  Это локальные данные (PGN Lichess и сгенерированный JSONL), а не
  исходники; в git им не место.
- **Почему Kaggle/Colab отдельным разделом.** Бесплатные T4 16 GB и
  прерываемые сессии — другой сценарий, чем A5000/4090; 4B и
  `batch=1/max_length=1024` дают реалистичную точку входа.
