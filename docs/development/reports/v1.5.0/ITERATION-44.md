# Итерация 44 — Fixing LoRA Training Pipeline & GPU Guide (T-090, T-091)

**Дата:** 2026-10-31
**Статус:** ✅ завершена локально — целевые тесты и линтеры зелёные;
реальный прогон на A5000 и полная backend-сюита — следующие шаги (GPU/CI).
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `docs/development/reports/v1.5.0/ITERATION-44.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `scripts/train_lora.py` (1088 строк) | `collate_batch` возвращает `torch.Tensor` (`input_ids`/`attention_mask`/`labels`, `torch.long`); добавлен ленивый `_torch_tensor`; `TooLongError` и `--skip-too-long` (default True); токенизация идёт до загрузки модели, длинные записи считаются в `skipped_too_long`; из `TrainingArguments` убраны `logging_dir` и `warmup_ratio`; `--no-skip-too-long` возвращает падение |
| `tests/unit/test_train_lora.py` (1196 строк) | 73 теста: `collate_batch` на моке `torch.tensor` (тип/dtype/padding), парсер `--skip-too-long`, `train()` с пропуском длинной записи и падением при `--no-skip-too-long`, проверка отсутствия `logging_dir`/`warmup_ratio` |
| `pyproject.toml` | mypy-override для опциональных `torch`/`torchvision`/`transformers`/`peft`/`datasets`/`accelerate`/`bitsandbytes`: `mypy` скриптов работает и без них (CI), и с ними (GPU) |
| `docs/development/GPU_TRAINING_GUIDE.md` (989 строк) | §2: `torch` + `torchvision` одной сборкой `cu126`, зеркало SJTU, `uv`, проверка обеих версий; §10: CUDA 12.6, `hf download` + `HF_ENDPOINT=https://hf-mirror.com` + `snapshot_download`, A5000, `--skip-too-long`, новые строки траблшутинга |
| `TODO.md` | T-090 и T-091 отмечены `[x]` |
| `docs/CHANGELOG.md` | `[Unreleased]`: секции `Fixed`/`Changed` для T-090/T-091 |
| `docs/development/RELEASES.md` | Запись об итерации 44 |

### Рабочее дерево

Помимо изменений этой итерации, в дереве есть незакоммиченные изменения
прошлых итераций (T-087…T-089) и параллельной работы (`README.md`,
`docs/development/GETTING_STARTED.md`, `scripts/install_stockfish.py`,
`tests/unit/test_install_stockfish.py`, `tests/integration/test_auth_api.py`,
`docker-compose.yml`, `chess_code.tar.gz`). Они не затрагивались.

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| `collate_batch` | `test_train_lora.py` | Мок `torch.tensor` через `sys.modules`; все три поля — объекты-тензоры, dtype `torch.long`; right-padding; маска padding; labels padding `-100`; пустой батч |
| `TooLongError` | `test_train_lora.py` | `tokenize_example` при prompt, не оставляющем места assistant, поднимает `TooLongError` (подкласс `ValueError`) |
| `--skip-too-long` | `test_train_lora.py` | Парсер: default `True`, `--no-skip-too-long` → `False`; `train()` с длинной + валидной записью оставляет одну, печатает `skipped_too_long`; с `--no-skip-too-long` — `SystemExit` |
| `TrainingArguments` | `test_train_lora.py` | В kwargs нет `logging_dir` и `warmup_ratio`; `report_to`/`resume` по-прежнему передаются |
| Resume/report (регрессия) | `test_train_lora.py` | Полный `train()` с fake-стеком, `Trainer.train(resume_from_checkpoint=...)`, `report_to`, сохранение модели |
| GPU-гайд | `GPU_TRAINING_GUIDE.md` | Команды `hf download`, `HF_ENDPOINT`, `snapshot_download`, `uv`, `cu126`-зеркало, `torchvision`, проверка версий, траблшутинг |
| Регрессия | связанный набор | **204 passed**: train, export GGUF, generator датасета, dataset export, внешний конвертер, промпты |

Новых тестов: **+3** (парсер `--skip-too-long`, пропуск длинной записи,
падение с `--no-skip-too-long`); тест `collate_batch` переписан под
тензоры. Frontend не менялся.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Тесты обучения | `pytest tests/unit/test_train_lora.py -q` | **73 passed in 1.62 s** (exit 0) |
| Тесты экспортёра (регрессия) | `pytest tests/unit/test_export_to_gguf.py -q` | **18 passed** (exit 0) |
| Связанный набор | train + export + generator + dataset_export + convert_external + prompts | **204 passed in 4.93 s** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Типы скриптов/тестов | `mypy scripts/train_lora.py scripts/export_to_gguf.py tests/unit/test_train_lora.py` | `Success: no issues found in 3 source files` |
| Полная backend-сюита | `pytest -q` | локально не завершена (нет Stockfish в `PATH`, нестандартный PostgreSQL 5437); проверка — CI |

**Smoke `collate_batch` на реальном torch:**

```text
input_ids Tensor torch.int64 [[1, 2], [3, 0]]
attention_mask Tensor torch.int64 [[1, 1], [1, 0]]
labels Tensor torch.int64 [[-100, 5], [7, -100]]
```

**Dry-run на gold-датасете:**

```text
Ограничение --limit: используется 3 из 5000 записей
Записей: 3
Распределение по schema_version: 2=3
...
report_to=auto, resume=нет, skip_too_long=да
DRY-RUN OK: схема JSONL корректна, статистика посчитана, обучение не запускалось.
```

**CLI:**

```text
[--skip-too-long | --no-skip-too-long]
[--report_to {auto,tensorboard,wandb,none}]
[--resume_from_checkpoint PATH]
```

---

## 4. Smoke

1. **Тензоры вместо списков.** `collate_batch` на реальном `torch` вернул
   три `Tensor` `torch.int64` с ожидаемыми значениями (см. §3) — это
   закрывает `TypeError: embedding(): argument 'indices' must be Tensor,
   not list`.
2. **Пропуск длинных записей.** Unit-сценарий с `--max-length 100`:
   длинная запись отброшена, валидная осталась, в выводе
   `skipped_too_long`; `trainer.train` вызван. С `--no-skip-too-long` —
   `SystemExit` до загрузки модели.
3. **`TrainingArguments`.** Fake-стек фиксирует отсутствие `logging_dir`
   и `warmup_ratio`; `report_to` и `resume_from_checkpoint` доходят до
   `Trainer`.
4. **CLI.** `--help` показывает `--skip-too-long/--no-skip-too-long`,
   `--report_to`, `--resume_from_checkpoint`; `--dry-run` печатает
   `skip_too_long=да`.
5. **Реальный A5000 не запускался:** в локальной среде нет GPU; проверка
   ограничена unit-тестами, реальным `torch.tensor` и dry-run.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| Реальное обучение на A5000 не выполнено | Нет GPU в локальной среде; исправления проверены unit-тестами и smoke `collate_batch`, но первая реальная итерация — на арендованной машине |
| Полная backend-сюита локально не прогнана | Окружение без Stockfish и с нестандартным PostgreSQL; по договорённости — CI |
| `--skip-too-long` скрывает часть данных | Пропущенные записи видны в `skipped_too_long`, но их можно не заметить; при большом N стоит увеличить `--max-length` или проверить датасет |
| Все записи длинные → `SystemExit` | Если после фильтра не осталось ни одной записи, обучение не стартует — это защита от пустого датасета; сообщение предлагает увеличить `--max-length` |
| Удаление `logging_dir`/`warmup_ratio` меняет поведение | TensorBoard-логи теперь идут в default `output_dir/runs` (зависит от версии transformers), learning-rate warmup — default 0.0; при необходимости задавайте через актуальные аргументы версии |
| HF-зеркало — внешний сервис | `hf-mirror.com` может отставать/быть недоступным; gated-модели требуют `HF_TOKEN` с официального сайта, зеркало логин не поддерживает |
| Команды `uv`/SJTU-зеркала могут измениться | Инструкция отражает актуальные URL; при недоступности используйте официальный PyTorch index |
| Смешать `torch`/`torchvision` всё ещё можно вручную | Код не проверяет версии CUDA; защита — команда установки и проверка в §2, а не runtime-валидация |
| Чужие незакоммиченные изменения | README/GETTING_STARTED/install_stockfish/auth-тест/архив изменены параллельно; не затрагивались |

---

## 6. Новые KI

**Нет.** Новых `KI-NNN` не заводилось: дефекты (списки вместо тензоров,
несовместимые аргументы `TrainingArguments`, падение на длинной записи)
исправлены и покрыты тестами, линтеры и типы зелёные. Реальный прогон на
A5000 — проверка окружения, а не известный дефект кода; KI-030 (качество
LoRA/LLM) остаётся открытым.

---

## 7. Обоснование

- **Почему `collate_batch` возвращает тензоры.** `Trainer` передаёт батч
  напрямую в `forward`; `embedding()` требует `Tensor`, а не list. Тензоры
  создаются `torch.long`, потому что input_ids/labels — индексы.
- **Почему импорт torch ленивый.** `--dry-run` и `--check-env` должны
  работать без torch (CI/локальный venv); `_torch_tensor` импортирует
  библиотеку только при реальном батче и даёт понятный `INSTALL_HINT`.
- **Почему тест мокает `torch.tensor`, а не грузит модель.** Проверяется
  контракт (`Tensor`, dtype, padding) без гигабайтов весов и GPU.
- **Почему убраны `logging_dir` и `warmup_ratio`.** В transformers 4.44+
  они вызывают `TypeError`; TensorBoard-интеграция использует default
  `output_dir/runs`, а warmup по умолчанию 0.0. Это conscious trade-off:
  меньше управления, зато обучение запускается.
- **Почему `--skip-too-long` по умолчанию True.** Одна длинная запись не
  должна убивать многочасовой прогон на арендованной GPU. Счётчик и
  предупреждения делают пропуск видимым; `--no-skip-too-long` оставлен для
  строгой проверки датасета.
- **Почему отдельный `TooLongError`.** Иначе `ValueError` от «нет
  assistant»/«нет общего префикса» смешивался бы с «не влезло в
  max_length», и нельзя было бы пропускать только длинные записи.
- **Почему токенизация до загрузки модели.** Если все записи длинные или
  битые, незачем скачивать/поднимать 7B на GPU; ошибка видна сразу.
- **Почему `Dataset.from_list` вместо `map` + `filter`.** Один проход
  токенизации, явный счётчик `skipped_too_long` и без двойного вызова
  tokenizer для каждой записи.
- **Почему `hf download` + `HF_ENDPOINT`.** `huggingface-cli download`
  устарел; `hf` — актуальный CLI, а `HF_ENDPOINT` переключает весь
  huggingface_hub на зеркало без правок кода.
- **Почему `torch` и `torchvision` одной командой.** Разные CUDA-сборки
  дают runtime-ошибку; установка обеих с `cu126` из одного индекса
  гарантирует совместимость. `uv` и SJTU-зеркало уменьшают таймауты из РФ.
- **Почему mypy-override для опциональных пакетов.** `torch`/`transformers`/
  `peft`/`datasets` не входят в `[project.dependencies]`; без override
  `mypy scripts/...` падал бы в CI без них, а с `# type: ignore` —
  ругался бы на неиспользуемый ignore на GPU-машине.
