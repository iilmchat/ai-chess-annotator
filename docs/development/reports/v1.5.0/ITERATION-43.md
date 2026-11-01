# Итерация 43 — GGUF Exporter & Training Script Polish (T-088, T-089)

**Дата:** 2026-10-31
**Статус:** ✅ завершена локально — целевые тесты и линтеры зелёные;
реальное обучение/merge/GGUF и полная backend-сюита выполняются на
GPU-машине и CI (локально нет GPU, torch/peft/llama.cpp).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `scripts/export_to_gguf.py` (396 строк) | LoRA → merge через `peft` → HF `save_pretrained` → опционально GGUF через `llama.cpp`; CLI `--base_model/--adapter_path/--output_dir/--llama_cpp_dir/--gguf_outtype/--device`; graceful fallback без llama.cpp |
| `tests/unit/test_export_to_gguf.py` (424 строки) | 18 unit-тестов: проверка адаптера, поиск конвертера, команда subprocess, успех/сбой конвертации, merge/save HF, CLI, `main` без llama.cpp и с отсутствующим адаптером |
| `docs/development/reports/v1.5.0/ITERATION-43.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `scripts/train_lora.py` | `--resume_from_checkpoint PATH` (ранняя проверка каталога, передача в `Trainer.train`), `--report_to auto\|tensorboard\|wandb\|none`, `resolve_report_to`/`resolve_resume_checkpoint`, `logging_dir=output_dir/runs`, tensorboard/wandb в `--check-env` |
| `tests/unit/test_train_lora.py` | +11 тестов: парсер новых флагов, `resolve_report_to` (auto/tensorboard/wandb/none/отсутствие), `resolve_resume_checkpoint`, полный `train()` с fake-стеком и проверкой `Trainer.train(resume_from_checkpoint=...)` |
| `docs/development/GPU_TRAINING_GUIDE.md` | Новый §10 «Запуск на арендованном сервере (Ubuntu 22.04 + CUDA 12.1)»: зависимости, RTX 4090 (batch 4/grad 4), TensorBoard, resume, export GGUF, LM Studio; обновлены TOC, §6 и траблшутинг |
| `TODO.md` | T-088 и T-089 отмечены `[x]` |
| `docs/CHANGELOG.md` | Секция `[Unreleased]` → `Added`: экспортёр GGUF и resume/логирование |
| `docs/development/RELEASES.md` | Запись об итерации 43 |

### Рабочее дерево

В рабочем дереве также есть незакоммиченные изменения прошлой итерации 42
(`.gitignore`, `app/llm/prompts.py`, `tests/unit/test_prompts.py`,
`scripts/convert_external_datasets.py` и его тесты) и параллельной работы
(`README.md`, `docs/development/GETTING_STARTED.md`, `scripts/install_stockfish.py`,
`tests/unit/test_install_stockfish.py`, `docker-compose.yml`). Они не
затрагивались в T-088/T-089.

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| Валидация адаптера | `test_export_to_gguf.py` | Каталога нет; нет `adapter_config.json`; корректный каталог |
| Поиск llama.cpp | `test_export_to_gguf.py` | Явный `--llama_cpp_dir` с `convert_hf_to_gguf.py`; legacy `convert.py`; пустой каталог → `None` |
| Команда конвертера | `test_export_to_gguf.py` | `python converter MODEL --outfile ... --outtype f16`; `subprocess.run(check=True, capture_output=True, text=True)` |
| Конвертация | `test_export_to_gguf.py` | Успех создаёт `model.gguf`; `CalledProcessError` → warning/`False`; пустой вывод → warning/`False` |
| Merge/save | `test_export_to_gguf.py` | `AutoModel.from_pretrained` (fp16, `device_map=cpu/cuda`, `trust_remote_code`, `low_cpu_mem_usage`), `PeftModel.from_pretrained`, `merge_and_unload`, `save_pretrained(safe_serialization=True)`, сохранение токенизатора |
| CLI/fallback | `test_export_to_gguf.py` | Обязательные `--base_model/--adapter_path/--output_dir`; default `f16`/`cpu`; `main` без llama.cpp → exit 0 и HF; отсутствующий адаптер → exit 1 |
| Resume | `test_train_lora.py` | Парсер `--resume_from_checkpoint`; `None`/существующий каталог/отсутствующий путь → `SystemExit`; передача строки в `Trainer.train` |
| Логирование | `test_train_lora.py` | `--report_to auto` предпочитает TensorBoard, затем wandb; без библиотек — `[]`; явный отсутствующий — warning; `none` — `[]`; `logging_dir=output_dir/runs` |
| Полный `train()` | `test_train_lora.py` | Fake `Dataset`/`Trainer`/`TrainingArguments`: resume-путь доходит до `trainer.train`, `report_to=[]`, модель сохраняется в `--output-dir` |
| Регрессия | связанный набор | 201 passed: train, export, generator датасета, dataset export, внешний конвертер, промпты |

Новых тестов: **+29 backend** (18 экспортёр + 11 train). Frontend не
менялся.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Тесты экспортёра | `pytest tests/unit/test_export_to_gguf.py -q` | **18 passed in 0.07 s** (exit 0) |
| Тесты обучения | `pytest tests/unit/test_train_lora.py -q` | **70 passed in 1.61 s** (exit 0) |
| Общий целевой набор | `pytest tests/unit/test_export_to_gguf.py tests/unit/test_train_lora.py -q` | **88 passed in 1.73 s** |
| Связанная регрессия | train + export + generator + dataset_export + convert_external + prompts | **201 passed in 2.81 s** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Типы скриптов | `mypy scripts/export_to_gguf.py scripts/train_lora.py` | `Success: no issues found in 2 source files` |
| Полная backend-сюита | `pytest -q` | локально не завершена (нет Stockfish в `PATH`, нестандартный порт PostgreSQL 5437); проверка — на CI |

**Dry-run обучения на gold-датасете:**

```text
Ограничение --limit: используется 3 из 5000 записей
Записей: 3
Распределение по schema_version: 2=3
...
report_to=auto, resume=нет
DRY-RUN OK: схема JSONL корректна, статистика посчитана, обучение не запускалось.
```

**CLI экспортёра:**

```text
usage: export_to_gguf.py [-h] --base_model MODEL --adapter_path PATH
                         --output_dir PATH [--llama_cpp_dir PATH]
                         [--gguf_outtype {f16,bf16,q8_0}]
                         [--device {cpu,cuda,auto}]
```

---

## 4. Smoke

1. **`train_lora.py --dry-run`** на `data/lora_gold_v1.jsonl` (`--limit 3`)
   → `schema_version: 2=3`, `report_to=auto`, `resume=нет`, `DRY-RUN OK`.
   Новые флаги не сломали офлайн-режим без torch.
2. **`train_lora.py --help`** содержит `--report_to
   {auto,tensorboard,wandb,none}` и `--resume_from_checkpoint PATH`.
3. **`export_to_gguf.py --help`** показывает все шесть флагов; модуль
   импортируется без torch/peft/transformers (тяжёлые импорты — только в
   момент экспорта).
4. **Юнит-сценарий merge/save:** fake-стек фиксирует
   `AutoModelForCausalLM.from_pretrained(..., torch_dtype=float16,
   device_map="cpu", low_cpu_mem_usage=True)`, `PeftModel.from_pretrained`,
   `merge_and_unload`, `save_pretrained(safe_serialization=True)` и
   `tokenizer.save_pretrained`.
5. **Юнит-сценарий fallback:** при `find_llama_cpp_converter → None` или
   `CalledProcessError` печатается warning, HF-модель остаётся, `main`
   возвращает 0. При отсутствующем адаптере `main` возвращает 1.
6. **Реальный GGUF не создавался:** в локальном окружении нет
   torch/peft/transformers/llama.cpp; это проверяется на GPU-машине по §10.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| Реальный merge/GGUF не выполнен | Нет GPU, torch/peft/transformers и llama.cpp; логика покрыта моками. Реальный прогон — §10 GPU-гайда |
| Реальное обучение с resume не выполнено | Нет GPU; протестирована передача `resume_from_checkpoint` в fake-Trainer, не восстановление optimizer на реальном чекпоинте |
| Полная backend-сюита локально не прогнана | Окружение без Stockfish и с нестандартным PostgreSQL; по договорённости — CI |
| Merge 7B на CPU требует ~16 ГБ RAM | `export_to_gguf.py` по умолчанию мерджит на CPU (`--device cpu`); на слабой машине нужен `--device cuda` или больше RAM |
| Нет авто-поиска «последнего» чекпоинта | `--resume_from_checkpoint` требует явный путь `checkpoint-*`; автовыбор `latest` не делали, чтобы не угадывать |
| `--report_to auto` предпочитает TensorBoard | Если установлены и TensorBoard, и wandb, выбран TensorBoard; wandb — только явно |
| GGUF не квантуется автоматически | По умолчанию `--gguf_outtype f16`; Q4_K_M — отдельной командой `llama-quantize` (описано в §10.5) |
| Ошибка конвертера llama.cpp → exit 0 | Осознанный graceful fallback: HF-модель сохранена, но пользователь должен увидеть warning и повторить конвертацию |
| LM Studio вживую не проверялся | Нет скачанной GGUF-модели; шаги импорта и `.env` описаны, но не пройдены на практике |
| Незакоммиченные чужие изменения | README/GETTING_STARTED/install_stockfish и `docker-compose.yml` изменены параллельно; не затрагивались |

---

## 6. Новые KI

**Нет.** Новых `KI-NNN` не заводилось: новые функции покрыты unit-тестами,
линтеры и типы зелёные, graceful fallback для llama.cpp реализован явно.
Непроверенные на реальном GPU merge/обучение и LM Studio — известные
ограничения среды, зафиксированы в §5. KI-030 (качество LoRA/LLM) остаётся
открытым и к T-088/T-089 не относится.

---

## 7. Обоснование

- **Почему merge в fp16, а не в 4-bit.** QLoRA-адаптер обучался на 4-bit
  базе, но `merge_and_unload` над квантованной моделью даёт
  непредсказуемое качество/ошибки. Загружаем базу в fp16, мерджим и
  сохраняем полноточную HF-модель — она же нужна GGUF-конвертеру.
- **Почему по умолчанию CPU.** Merge не требует GPU, а на арендованной
  машине GPU может быть занята/недоступна; `--device cuda` оставлен для
  скорости. Цена — ~16 ГБ RAM для 7B, это указано в §10.
- **Почему сначала HF, потом GGUF.** HF-модель — источник истины: если
  llama.cpp нет или конвертер упал, не нужно повторять обучение/merge.
  GGUF можно получить позже одной командой.
- **Почему отсутствие llama.cpp — не ошибка.** Постановка требует
  graceful fallback: HF-формат уже пригоден для transformers/локального
  инференса, а установка llama.cpp — отдельный шаг. Exit 0 + warning
  сохраняет пайплайн автоматизируемым.
- **Почему явный `--llama_cpp_dir` не дополняется эвристиками.** Если
  пользователь указал каталог, поиск «где-то ещё» может взять чужой
  конвертер. Без флага проверяются типовые пути и `PATH`.
- **Почему `--resume_from_checkpoint` проверяется до загрузки модели.**
  Иначе опечатка в пути обнаруживалась бы после скачивания/загрузки
  гигабайтов. `resolve_resume_checkpoint` падает сразу и подсказывает
  `checkpoint-*`.
- **Почему путь передаётся строкой в `Trainer.train`.** Так его принимает
  transformers; строка также удобна для логов. `None` означает «с нуля»,
  обучение не меняется.
- **Почему `--report_to auto`.** TensorBoard/wandb — опциональные
  зависимости; жёсткий импорт сломал бы обучение на машине без них.
  `auto` выбирает установленную интеграцию, `none` — только консоль,
  явный выбор даёт предсказуемость.
- **Почему `logging_dir=output_dir/runs`.** Логи лежат рядом с адаптером
  и чекпоинтами, их легко скачать/архивировать; TensorBoard запускается
  одной командой.
- **Почему тесты на fake-стеке.** Реальный merge 7B требует десятков ГБ
  RAM/VRAM; unit-тесты проверяют контракт вызовов (`from_pretrained`,
  `merge_and_unload`, `save_pretrained`, `subprocess.run`) без весов.
- **Почему §10 отдельным разделом.** Арендованный сервер — другой
  сценарий (Ubuntu, CUDA 12.1, 4090, batch 4/grad 4, resume, GGUF,
  LM Studio), его нельзя смешивать с инструкцией для локальной 3060.
