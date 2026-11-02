# Итерация 47 — Colab/T4 Compatibility Fixes & Dataset Truncation (T-095)

**Дата:** 2026-11-02
**Статус:** ✅ завершена локально — код, ноутбук, гайд, тесты, ruff и mypy
зелёные; реальный прогон на Colab/T4 и полная backend-сюита — CI/GPU.
**Проверка исходного состояния:** на момент проверки итерация 47 **не была
выполнена**: `ITERATION-47.md` отсутствовал, у `build_fact_package` не было
`max_tokens`, в `train_lora.py` — блокировки TF и `--force-fp16`, в ноутбуке
— `torchao`/TF-ячейки/`--no-4bit`. В рабочем дереве лежали только
незакоммиченные правки итерации 46 (T-094). Всё перечисленное реализовано в
этом проходе.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `docs/development/reports/v1.5.0/ITERATION-47.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/llm/prompts.py` | `build_fact_package(..., max_tokens=2048)`: длинная партия сокращается до первых/последних 10 ходов + ходов ключевых моментов; пометка о сокращении; окно сужается (10→8→…→0), затем при необходимости отбрасываются наименее важные ключевые моменты; `_format_game_moves` принимает `keeps`, `_render_package`/`_kept_plies` выделены; `ValueError` при `max_tokens < 1` |
| `scripts/train_lora.py` | На импорте `USE_TF=0`, `TRANSFORMERS_NO_TF=1` (до ленивого импорта torch/transformers); `_looks_like_t4`, `_force_fp16`; `_training_dtype(..., force_fp16=)`; `--force-fp16`; T4 принудительно fp16 в `_prepare_model` и `TrainingArguments`; `--check-env` предупреждает о T4; docstring/пример обновлены |
| `notebooks/kaggle_train_lora.ipynb` | `%pip install -q --upgrade "torchao>=0.16.0" --no-deps`; новая ячейка 1.1 блокировки TF до импорта torch; запуск с `--no-4bit`, `--force-fp16`, `--max-length 2048`; модель по умолчанию `Qwen/Qwen3-4B-Instruct-2507` (7B — комментарием, только с `--load-in-4bit`/1024); траблшутинг расширен |
| `tests/unit/test_prompts.py` | 6 новых тестов обрезки (дефолтный лимит, короткая партия без пометки, 1200-ходовая партия, кастомный лимит, PGN без анализа, `max_tokens=0`); helper `_long_pgn`/`_long_analyses`, module-fixture `long_game`; убран неиспользуемый `type: ignore` |
| `tests/unit/test_train_lora.py` | 6 новых тестов: TF-переменные, CLI `--force-fp16`, fp16 при флаге, fp16 на T4 при «bf16=True», `TrainingArguments` при `--force-fp16`, автоопределение T4; fake-стек параметризован `device_name` |
| `tests/unit/test_kaggle_notebook.py` | Проверки `torchao>=0.16.0`/`--no-deps`, TF-ячейки и её порядка до импорта torch, `--no-4bit`/`--force-fp16`, 4B-модели |
| `docs/development/GPU_TRAINING_GUIDE.md` | §11.1 — новые параметры ноутбука; §11.3 (Colab) — установка `torchao`, блокировка TF, команда с `--no-4bit --force-fp16 --max-length 2048`; новый §11.3.1 «Решение проблем (Troubleshooting)» (libcudart/TF, torchao, bf16/T4, `--no-4bit`, OOM, `skipped_too_long`); §11.4 и чек-лист обновлены |
| `TODO.md` | Новая T-095 (итерация 47) |
| `docs/CHANGELOG.md` | `[Unreleased]`: Fixed — обрезка пакета, TF-блокировка, `--force-fp16`/T4; Changed — Colab/T4-профиль ноутбука и §11.3.1 |
| `docs/development/RELEASES.md` | Запись об итерации 47 |

### Рабочее дерево

Незакоммиченными остаются также правки итерации 46 (T-094):
`app/rag/context.py`, `scripts/build_gold_dataset.py`,
`tests/fixtures/lora_sample_v2.jsonl`, `tests/integration/test_rag.py`,
`tests/unit/test_build_gold_dataset.py`, `test_dataset_export.py`,
`test_generate_lora_dataset.py`, `notebooks/`,
`tests/unit/test_kaggle_notebook.py`, `ITERATION-46.md`. Они не относятся к
T-095 и не переписывались, кроме ноутбука/`test_kaggle_notebook.py`
(общие артефакты двух итераций). `data/gold_dataset.jsonl` и
`data/gold_pgns/` — локальные gitignored-артефакты.

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| Обрезка пакета | `test_prompts.py` | `MAX_FACT_PACKAGE_TOKENS == 2048`, `MOVES_HEAD_KEEP == MOVES_TAIL_KEEP == 10`; короткая партия без пометки и без «…»; 1200-ходовая партия: пакет ≤ 2048 оценённых токенов, есть пометка, начало `1. Nf3 Nf6`, конец `600. Ng1 Ng8`, ход ключевого момента `300. Ng1 Ng8`, середина `150.` отсутствует, 3/3 момента; кастомный лимит 500 и 3/3 момента; PGN без анализа тоже обрезается; `max_tokens=0` → `ValueError` |
| TF / dtype / CLI | `test_train_lora.py` | `USE_TF=0`/`TRANSFORMERS_NO_TF=1` после импорта модуля; `--force-fp16` в парсере; fp16 в `torch_dtype`/`bnb_4bit_compute_dtype` при флаге; fp16 на «Tesla T4» даже при `is_bf16_supported() == True`; `TrainingArguments(bf16=False, fp16=True)` при флаге и при T4; bf16-ветка на Ampere не сломана |
| Ноутбук | `test_kaggle_notebook.py` | Валидный JSON/nbformat 4.5, уникальные id, пустые outputs; `torchao>=0.16.0` + `--no-deps`; TF-ячейка (`USE_TF`/`TRANSFORMERS_NO_TF`, выгрузка `tensorflow` из `sys.modules`) стоит до ячейки с `import torch`; `Qwen/Qwen3-4B-Instruct-2507`, `BATCH_SIZE=1`, `GRAD_ACCUM=16`, `MAX_LENGTH=2048`, `--no-4bit`, `--force-fp16`, нет `--load-in-4bit`; fallback 7B и resume; нет «полуход» |
| Регрессия | связанный набор | **239 passed** (prompts, train_lora, kaggle, build_gold, dataset_export, generate_lora, convert_external); полная unit-сюита — **844 passed** |

Новых тестов: **13** (6 prompts, 6 train_lora, 1 notebook); ещё 3 теста
ноутбука обновлены под новые флаги/модель, fake-стек `train_lora`
параметризован `device_name`. Frontend не менялся.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.
**Tests:** 239/239 passed в связанном наборе; 844 passed, 1 skipped,
1 xfailed — полная unit-сюита.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Связанный набор | `pytest test_prompts test_train_lora test_kaggle_notebook test_build_gold_dataset test_dataset_export test_generate_lora_dataset test_convert_external_datasets -q` | **239 passed in 9.38 s** |
| Полная unit-сюита | `pytest tests/unit -q` | **844 passed, 1 skipped, 1 xfailed in 24.96 s** (skip — `cairosvg`; xfail — KI-012) |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Типы изменённых файлов | `mypy app scripts/train_lora.py scripts/build_gold_dataset.py tests/unit/test_kaggle_notebook.py tests/unit/test_train_lora.py tests/unit/test_prompts.py` | `Success: no issues found in 95 source files` |
| JSON ноутбука | `python -m json.tool notebooks/kaggle_train_lora.ipynb` | валиден |
| Полная backend-сюита | `pytest -q` | локально не запускалась: нужны PostgreSQL/Stockfish; проверка — CI |

`nbformat` в локальном venv не установлен, поэтому ноутбук проверен
`json.tool` и контрактными тестами `test_kaggle_notebook.py`, а не
`nbformat.validate`.

---

## 4. Smoke

1. **Обрезка пакета.** Партия-«карусель» из 1200 ходов, 3 ключевых
   момента (1, 600, 1200), уровень `beginner`:

   ```text
   plies: 1200
   full tokens: 2141 | capped tokens: 274 | limit: 2048
   moves: 1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 Nf6 … 300. Ng1 Ng8 …
   moves tail: … 596. Ng1 Ng8 597. Nf3 Nf6 598. Ng1 Ng8 599. Nf3 Nf6 600. Ng1 Ng8
   note: Партия сокращена по лимиту токенов: показаны начало, конец и ходы из ключевых моментов.
   key moments: 3
   ```

   Середина партии отсутствует, ключевой ход `300. Ng1 Ng8` и конец
   сохранены; полный пакет (2141) укладывается в лимит после обрезки.
2. **Dry-run обучения.** `train_lora.py --dry-run --dataset
   data/gold_dataset.jsonl --max-length 2048`:

   ```text
   Записей: 1000
   schema_version: 2=1000
   Оценка токенов: всего ~1471905, в среднем ~1471 на запись, максимум ~1810
   DRY-RUN OK: схема JSONL корректна, статистика посчитана, обучение не запускалось.
   ```

3. **TF-блокировка.** `python -c "import os, scripts.train_lora; …"` →
   `USE_TF=0`, `TRANSFORMERS_NO_TF=1` (переменные выставляются на импорте,
   до ленивого импорта torch/transformers).
4. **CLI/типы.** `--force-fp16` доступен в `--help`;
   unit-тесты подтверждают `fp16=True`/`bf16=False` в `TrainingArguments`
   и `float16` в `torch_dtype`/`bnb_4bit_compute_dtype` на T4.
5. **Ноутбук.** JSON валиден, code-ячейки без outputs; TF-ячейка идёт до
   ячейки `import torch`; в команде обучения `--no-4bit --force-fp16
   --max-length 2048`.
6. **Реальный Colab/T4 не запускался:** в локальной среде нет GPU и
   доступа к Colab; контракт проверен статически и unit-тестами.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| Реальный Colab/T4 не проверен | Ноутбук не исполнялся на T4; `--no-4bit` + 4B + `max_length=2048` должен помещаться в 16 GB, но это не измерено. При OOM — `--max-length 1536`/`1024` |
| `max_tokens` — оценка, не токенизатор | Лимит считается `estimate_tokens` (~4 символа/токен), а Qwen-токенизатор на русском даёт ~2.9–3.1 символа/токен (замер по 1000 записям). Поэтому 2048 «оценённых» токенов ≈ 6000–6500 символов, а не 2048 реальных токенов |
| Почему 335 пропусков могут остаться | На локальном датасете пакет фактов ≤ 2814 символов (~940 реальных токенов), то есть кэп 2048 на него **не срабатывает**. Пропуски при `--max-length 2048` вызваны суммой system (2642 символа) + user + assistant (медиана 1695): медиана записи ~5900 символов ≈ 2000 реальных токенов. Решение — поднять `--max-length` до 3072 (зафиксировано в §11.3.1), а не только резать пакет |
| Модель ноутбука изменена | Итерация 46 требовала Qwen2.5-7B, но 7B без 4-bit не помещается в T4 16 GB. По умолчанию взята 4B; 7B оставлена комментарием с `--load-in-4bit`/1024 — это осознанное отклонение от буквы итерации 46 ради работоспособности ноутбука |
| Экстремально малый `max_tokens` | Эскалация «окно → 0 → отбрасывание моментов → `ValueError`» покрыта тестом только на `max_tokens=0`; путь отбрасывания моментов на практике не воспроизведён |
| `nbformat` не проверен | Пакет не установлен в локальном venv; структура проверена `json.tool` и контрактными тестами |
| Старая фикстура/данные | `tests/fixtures/lora_sample_v2.jsonl` и локальный `data/gold_dataset.jsonl` собраны до итерации 47; после пересборки пакеты длинных партий будут короче |
| Полная backend-сюита | Локально не запускалась (PostgreSQL/Stockfish); зелёный прогон ожидается в CI |
| Итерация 46 не закоммичена | Правки T-094 остаются в рабочем дереве; ноутбук и его тест общие для 46/47, поэтому коммит логично делать одним срезом |

---

## 6. Новые KI

**Нет.** Новых `KI-NNN` не заводилось: изменения покрыты unit-тестами,
ruff/mypy зелёные, отсутствие Colab/T4-прогона — ограничение окружения.
Расхождение «оценка токенов vs Qwen-токенизатор» и бюджет
system+assistant зафиксированы в §5 и в §11.3.1 гайда; при необходимости
выносится отдельной задачей (например, T-096: считать пакет реальным
токенизатором Qwen).

---

## 7. Обоснование

- **Почему кэп в `build_fact_package`, а не в сборщике датасета.** Пакет
  фактов — единый источник для golden-датасета, API комментариев, чата и
  экспорта. Ограничение в одном месте защищает все каналы и не требует
  дублировать обрезку в `build_gold_dataset.py`/`export_dataset.py`.
- **Почему первые/последние 10 ходов + ключевые моменты.** Модели нужны
  дебют (контекст плана) и концовка (чем закончилось), а середина без
  ключевых событий — балласт. Ключевые моменты уже отобраны по потере,
  поэтому их ходы добавляются в список SAN, чтобы `## Ход N …` не
  ссылался на отсутствующий ход.
- **Почему окно сужается 10→8→…→0, а потом режутся моменты.** Гарантия
  «пакет ≤ лимита» важнее сохранения полного списка: сначала жертвуем
  ходами, затем наименее важными моментами (они отсортированы по
  убыванию потери). Если не влезает даже пустой пакет — честный
  `ValueError`, а не молчаливое превышение.
- **Почему `estimate_tokens` проекта, а не токенизатор.** `build_fact_package`
  — синхронный слой без зависимостей от transformers; тащить Qwen-токенизатор
  в API/сборщик дорого. Оценка документирована как приблизительная, а
  реальная проверка остаётся за `train_lora.py` (`--max-length`).
- **Почему TF блокируется на импорте скрипта.** `torch`/`transformers`
  импортируются лениво внутри `train()`, поэтому верхнеуровневые
  `USE_TF`/`TRANSFORMERS_NO_TF` гарантированно выставляются раньше; это
  ровно тот ручной патч, который применялся в Colab.
- **Почему T4 определяется по имени устройства.** На части сборок Colab
  `torch.cuda.is_bf16_supported()` возвращает `True` на T4, после чего
  обучение зависает. Имя «Tesla T4» — единственный надёжный признак в
  рантайме; `--force-fp16` остаётся ручным переключателем для других
  сборок.
- **Почему 4B по умолчанию в ноутбуке.** `--no-4bit` — требование
  итерации 47 из-за нестабильного bitsandbytes в Colab; без квантования
  7B (≈14 GB весов) не оставляет места активациям на T4 16 GB. 4B в fp16
  (≈8 GB) — рабочая комбинация; 7B сохранена комментарием, чтобы не
  терять сценарий итерации 46.
- **Почему `--max-length 2048` оставлен, несмотря на замер.** Это явное
  требование постановки; фактический бюджет записи (system+user+assistant)
  вынесен в §11.3.1 как рекомендация поднять лимит до 3072, а не
  изменять постановку молча.
