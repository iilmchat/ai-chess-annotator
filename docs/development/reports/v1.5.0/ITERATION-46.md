# Итерация 46 — Prompt Terminology & Kaggle Notebook (T-094)

**Дата:** 2026-11-01
**Статус:** ✅ завершена локально — промпт, ноутбук, тесты, ruff и mypy
зелёные; реальный прогон на Kaggle/T4 и полная backend-сюита — CI/GPU.
**Формат:** RULES §2.18 (7 разделов).

---

## 1. Файлы (+/-)

### Новые файлы

| Файл | Назначение |
| :--- | :--- |
| `notebooks/kaggle_train_lora.ipynb` (22 ячейки) | Kaggle-ноутбук: зависимости, клон репозитория, автопоиск `gold_dataset.jsonl`, `--dry-run`, QLoRA Qwen2.5-7B на T4 (`batch=1`, `grad_accum=16`, `max_length=2048`, `--load-in-4bit`), TensorBoard, zip-адаптер, resume, траблшутинг |
| `tests/unit/test_kaggle_notebook.py` (5 тестов) | JSON/nbformat, чистые outputs, pip-зависимости, клон + датасет, T4-гиперпараметры и Qwen2.5-7B, fallback/resume, отсутствие «полуход» |
| `docs/development/reports/v1.5.0/ITERATION-46.md` | Этот отчёт |

### Изменённые файлы

| Файл | Изменение |
| :--- | :--- |
| `app/llm/prompts.py` | Метаданные — `Ходов: N` без счётчика полуходов; заголовок ключевого момента — `Ход N белых/чёрных` (номер хода, не ply); из docstring/комментариев убрано слово «полуход» |
| `app/rag/context.py` | RAG-контекст печатает `Ход N белых/чёрных` вместо номера полухода |
| `scripts/build_gold_dataset.py` | Формулировки «оценка после хода», «число ходов»; комментарий, что в пакет фактов попадает номер хода, а не служебный ply |
| `scripts/train_lora.py` | `_training_dtype`: bf16 на Ampere+, fp16 на T4/P100; dtype передаётся в `BitsAndBytesConfig`/`torch_dtype`; `TrainingArguments` получает `fp16=True`/`bf16=False`, если bf16 нет |
| `tests/unit/test_prompts.py` | Новая проверка метаданных; 3 параметризованных теста на отсутствие «полуход»; тест чёрного хода; тест system + user промпта |
| `tests/unit/test_build_gold_dataset.py` | Проверки `## 1. Ход 2 белых: Nf3` и отсутствия «полуход» в реальном mock-пакете; сообщение об ошибке — «ходов» |
| `tests/unit/test_train_lora.py` | 2 новых теста fp16-fallback (модель и `TrainingArguments`); проверки bf16/fp16 в существующем тесте; fake-стек параметризован `bf16` |
| `tests/unit/test_dataset_export.py`, `tests/unit/test_generate_lora_dataset.py` | Fake-пакет приведён к формату без «полуход» |
| `tests/integration/test_rag.py` | RAG-контекст проверяет `## Ход 1 белых (d4)` и отсутствие «полуход» |
| `tests/fixtures/lora_sample_v2.jsonl` | `user`-пакеты пересобраны без «полуход»; `fact_sheet_chars` синхронизирован |
| `docs/development/GPU_TRAINING_GUIDE.md` | §11.1 «Как запустить обучение на Kaggle за 5 минут» со ссылкой на ноутбук; §11.2–§11.4 перенумерованы; примеры на `gold_dataset.jsonl`; T4-оговорка 7B/2048 и fallback 4B/1024; fp16-режим |
| `TODO.md` | T-092 — прогресс **130 записей**; новая T-094 (итерация 46) |
| `docs/CHANGELOG.md` | `[Unreleased]`: Fixed (терминология, T4 fp16), Changed (гайд §11.1), Added (ноутбук, прогресс 130 записей) |
| `docs/development/RELEASES.md` | Запись об итерации 46 |

### Рабочее дерево

Незакоммиченных изменений прошлых итераций в дереве нет; все правки
относятся к T-094. `data/gold_dataset.jsonl` и `data/gold_pgns/` —
локальные gitignored-артефакты, в коммит не входят.

---

## 2. Покрытие

| Область | Файл тестов | Что покрыто |
| :--- | :--- | :--- |
| Пакет фактов | `test_prompts.py` | Метаданные `Ходов: 3`; `Ход 2 белых: c4`; `## 1. Ход 1 чёрных: Nf6`; отсутствие «полуход» для `beginner`/`club`/`gm`; отсутствие «полуход» в system + user промпте |
| Сборщик golden | `test_build_gold_dataset.py` | Реальный `build_fact_package` из `[%eval]`-PGN: `## 1. Ход 2 белых: Nf3`, `Потеря относительно лучшей линии: 160 cp`, `Лучший ход движка неизвестен`, нет «полуход»; сообщение об ошибке — «ходов» |
| T4 dtype | `test_train_lora.py` | `_prepare_model` в fp16 при `is_bf16_supported() == False` (и `bnb_4bit_compute_dtype`, и `torch_dtype`); `TrainingArguments` `bf16=False`/`fp16=True`; bf16-ветка по-прежнему `bf16=True`/`fp16=False` |
| RAG-контекст | `test_rag.py` | `## Ход 1 белых (d4)`; отсутствие «полуход» (integration, нужен PostgreSQL) |
| Kaggle Notebook | `test_kaggle_notebook.py` | Валидный JSON, nbformat 4.5, уникальные id, пустые `outputs`/`execution_count`; pip-пакеты; git clone и `gold_dataset.jsonl`; `--dry-run`; `MODEL_NAME = Qwen/Qwen2.5-7B-Instruct`, `BATCH_SIZE=1`, `GRAD_ACCUM=16`, `MAX_LENGTH=2048`, `--load-in-4bit`; HF-зеркало; fallback 4B/1024; resume; нет «полуход» |
| Регрессия | связанный набор | **244 passed**: prompts, build_gold, kaggle, train_lora, dataset_export, generate_lora_dataset, convert_external, export_to_gguf |

Новых тестов: **12** (5 prompt-кейсов, 2 T4-dtype, 5 notebook).
Обновлена фикстура `lora_sample_v2.jsonl`; frontend не менялся.

---

## 3. Результаты

**Build:** 0 ошибок / 0 предупреждений.
**Tests:** 244/244 passed в связанном наборе; 70/70 — в целевом
(prompts + build_gold + kaggle).

| Проверка | Команда | Результат |
| :--- | :--- | :--- |
| Промпт + сборщик + ноутбук | `pytest tests/unit/test_prompts.py tests/unit/test_build_gold_dataset.py tests/unit/test_kaggle_notebook.py -q` | **70 passed in 2.43 s** |
| Связанный набор | prompts + build_gold + kaggle + train_lora + dataset_export + generate_lora_dataset + convert_external + export_to_gguf | **244 passed in 3.46 s** |
| Линтер | `ruff check .` | `All checks passed!` |
| Типы | `mypy app` | `Success: no issues found in 90 source files` |
| Типы изменённых файлов | `mypy app scripts/build_gold_dataset.py scripts/train_lora.py tests/unit/test_kaggle_notebook.py` | `Success: no issues found in 93 source files` |
| JSON ноутбука | `python -m json.tool notebooks/kaggle_train_lora.ipynb` | валиден |
| Полная backend-сюита | `pytest -q` | локально не запускалась: нужны PostgreSQL/Stockfish; проверка — CI |

**Smoke сборщика (`--provider mock`):**

```text
Золотой датасет: schema_version=2
PGN-файлов: 1
Партий: 1
Записей: 1
Пропущено: 0
Средняя длина assistant: 291 символов
```

Фрагмент пакета фактов:

```text
Ходов: 2
...
## 1. Ход 2 белых: Nf3
- Сыграно: Nf3 (белые)
- Оценка после хода (за белых): -1.50 (-150 cp) за белых
- Потеря относительно лучшей линии: 160 cp; класс: mistake
- Лучший ход движка неизвестен: в PGN есть только оценка.
```

`HAS_POLUHOD False` — слова «полуход» в пакете нет.

**Dry-run обучения:**

```text
Файл: data\gold_dataset.jsonl
Записей: 19
schema_version: 2=19
...
DRY-RUN OK: формат JSONL корректен, роли на месте, записи не превышают лимит.
```

---

## 4. Smoke

1. **Промпт.** `build_fact_package` на PGN `1. e4 e5 2. Nf3 Nc6` с
   `[%eval]` даёт `Ходов: 2` и `## 1. Ход 2 белых: Nf3`; строка
   `(полуход 3)` не появляется. Для чёрного хода — `## 1. Ход 1 чёрных:
   Nf6`.
2. **Сборщик.** `build_gold_dataset.py --provider mock --limit 1` на
   временном PGN с `[%eval 0.20 ... -1.40]` создал 1 запись
   `schema_version=2`; mock-ответ прошёл валидатор; в `user` — новый
   формат без «полуход».
3. **Обучение.** `train_lora.py --dry-run --dataset
   data/gold_dataset.jsonl --max-length 2048` прочитал 19 записей,
   подтвердил `schema_version=2`, роли и длины; `DRY-RUN OK`.
4. **T4 dtype.** Unit-тест с fake-torch (`is_bf16_supported() == False`)
   подтверждает `torch_dtype=float16`, `bnb_4bit_compute_dtype=float16`
   и `TrainingArguments(fp16=True, bf16=False)`.
5. **Ноутбук.** JSON валиден, 22 ячейки, code-ячейки без outputs;
   `test_kaggle_notebook.py` проверяет pip, clone, датасет, dry-run,
   Qwen2.5-7B, T4-параметры, HF-зеркало, fallback и resume.
6. **Реальный Kaggle/T4 не запускался:** в локальной среде нет Kaggle
   GPU и доступа к скачиванию Qwen2.5-7B; контракт ноутбука проверен
   статически и unit-тестом.

---

## 5. Что не так

| Ограничение | Детали |
| :--- | :--- |
| Реальный Kaggle/T4 не проверен | Ноутбук не исполнялся на Kaggle; 7B в NF4 с `max_length=2048` на T4 16 GB — верхняя граница, возможен OOM |
| 7B/2048 — риск | Постановка требует Qwen2.5-7B и 2048; для стабильного T4-прогона в ноутбуке и гайде предусмотрен fallback `Qwen3-4B` + `max_length=1024` |
| 130 записей локально не воспроизведены | В рабочем дереве `data/gold_dataset.jsonl` — **19** записей (gitignored); 130 зафиксированы по постановке и в TODO/CHANGELOG. Для Kaggle нужен отдельный Dataset с полным файлом |
| Старые записи собраны до фикса | В локальных 19 записях `user` ещё содержит «полуход» (19/19), `assistant` — 6/19; для чистого обучения датасет надо пересобрать или нормализовать |
| RAG-контекст | `app/rag/context.py` переведён на «Ход N», но отдельного unit-теста на заголовок нет; покрытие — существующий integration-тест RAG (нужен PostgreSQL) |
| Ноутбук клонирует `main` | Если коммит с ноутбуком не запушен, Kaggle увидит старую версию; для приватного репозитория нужен GitHub-токен |
| Полная backend-сюита | Локально не запускалась (PostgreSQL/Stockfish); зелёный прогон ожидается в CI |
| Qwen2.5-7B vs дефолт проекта | `train_lora.py` по умолчанию Qwen3-4B; ноутбук явно передаёт `--model Qwen/Qwen2.5-7B-Instruct`, чтобы выполнить постановку |

---

## 6. Новые KI

**Нет.** Новых `KI-NNN` не заводилось: изменения покрыты unit-тестами,
ruff/mypy зелёные, отсутствие 7B/T4-прогона — ограничение окружения, а не
дефект кода. KI-030 (качество LoRA/LLM) остаётся открытым.

---

## 7. Обоснование

- **Почему правится `build_fact_package`, а не только `build_gold_dataset`.**
  Пакет фактов — единый источник для golden-датасета, API и экспорта;
  исправление в одном месте убирает «полуход» из всех промптов и не
  требует дублировать форматирование в сборщике.
- **Почему «Ход 19 чёрных», а не «19...».** Это номер полного хода,
  который соответствует системному промпту `club` («Не используй запись
  «12...» — пиши «на 12-м ходу чёрных»»). Формат `19...` в пакете
  провоцировал бы модель копировать запрещённую нотацию; `Ход 19
  чёрных` однозначен и естественен для русского комментария.
- **Почему убран счётчик полуходов из метаданных.** `Ходов: 40 (80
  полуходов)` — противоречивая и ненужная деталь: список SAN уже содержит
  все ходы, а слово «полуход» — ровно то, что модель начала повторять.
- **Почему RAG-контекст тоже изменён.** Он попадает в промпт GM-уровня;
  оставлять там «Полуход 38» значило бы сохранить тот же раздражитель в
  другом канале.
- **Почему ноутбук, а не только `setup_kaggle.py`.** Notebook — исполняемая
  инструкция: ячейки видны, параметры редактируются, Output/zip и resume
  работают штатными средствами Kaggle; unit-тест проверяет контракт, чтобы
  ноутбук не «сгнил» при изменениях CLI.
- **Почему Qwen2.5-7B по умолчанию.** Это требование итерации 46; проект
  historical default — Qwen3-4B, поэтому ноутбук передаёт модель явно, а
  при OOM на T4 16 GB переключается на 4B/1024.
- **Почему fp16-fallback в `train_lora.py`.** T4 (Turing) не поддерживает
  bf16; жёсткий `torch.bfloat16` и `fp16=False` в `TrainingArguments` могли
  дать ошибку bitsandbytes или обучение без mixed precision. Автовыбор
  dtype делает ноутбук из §11.1 реально запускаемым, а не только
  документированным.
- **Почему `--dry-run` в ноутбуке до обучения.** Ошибка в датасете
  (schema/роли/длина) выявляется до загрузки 7B и траты GPU-часов.
- **Почему 130 записей зафиксированы в TODO/CHANGELOG, хотя локально 19.**
  Постановка фиксирует прогресс сбора; локальный JSONL — gitignored
  артефакт и мог быть перезаписан частичным прогоном. В отчёте оба числа
  указаны явно, чтобы не выдавать 19 за 130.
