# LoRA-дообучение Qwen-4B на правках редакторов (T-104)

**Статус:** пайплайн готов к запуску на GPU; локально обучение не
выполнялось (на машине разработки нет NVIDIA GPU и `torch`/`peft` —
см. §11 «Ограничения»). `scripts/train_lora.py --dry-run` работает
офлайн и проверяет датасет/гиперпараметры.

## Оглавление

1. [Зачем это](#1-зачем-это)
2. [Prerequisites](#2-prerequisites)
3. [Экспорт датасета](#3-экспорт-датасета)
4. [Схема JSONL](#4-схема-jsonl)
5. [Гиперпараметры](#5-гиперпараметры)
6. [Локальная проверка `--dry-run`](#6-локальная-проверка---dry-run)
7. [Реальное обучение на GPU](#7-реальное-обучение-на-gpu)
8. [Стоимость](#8-стоимость)
9. [Сохранение адаптера](#9-сохранение-адаптера)
10. [Подключение через vLLM](#10-подключение-через-vllm)
11. [Оценка качества](#11-оценка-качества)
12. [Ограничения и риски](#12-ограничения-и-риски)
13. [Траблшутинг](#13-траблшутинг)

---

## 1. Зачем это

Локальная модель `qwen/qwen3-4b-2507` комментирует партию для уровней
`beginner`/`club` (SPEC §3). По LLM-as-judge (KI-030) средняя оценка
`club` — **4.40/10** при пороге 7/10: модель путает атрибуцию оценок и
рекомендаций. Промпт-инженерия (итерация 16) дала потолок; следующий шаг —
LoRA-дообучение на правках редакторов (`edits`): модель учится писать в
стиле принятых правок, а не «сырых» ответов.

Пайплайн:

```
commentaries + edits (PostgreSQL)
        │  scripts/export_dataset.py --level club
        ▼
data/lora_dataset.jsonl  (system → user → assistant)
        │  scripts/train_lora.py --dry-run        (офлайн, без GPU)
        │  scripts/train_lora.py --load-in-4bit   (GPU: RunPod/Colab)
        ▼
models/lora-club-v1/     (PEFT-адаптер)
        │  LORA_ADAPTER_PATH=models/lora-club-v1
        ▼
vLLM --lora-modules lora-club-v1=models/lora-club-v1
        │  scripts/evaluate_llm.py --levels club
        ▼
LLM_EVAL: сравнение с baseline 4.40/10
```

**Почему QLoRA.** Базовая модель 4B в 4-bit (`--load-in-4bit`) + LoRA
`r=16`, `alpha=32` на `q_proj`/`v_proj` помещается в T4 16 GB / RTX 4090
24 GB и обучается за 1–3 часа на нескольких сотнях–тысячах примеров.

---

## 2. Prerequisites

**Железо и драйверы (только для реального обучения):**

- NVIDIA GPU с CUDA: T4 16 GB (Colab free), RTX 4090 24 GB, A100 40/80 GB
  (RunPod). Для QLoRA 4B хватает T4.
- Linux (RunPod/Colab). На Windows `bitsandbytes` требует отдельной
  сборки — используйте WSL2 или облако.
- ~10–15 GB диска: веса Qwen-4B (≈8 GB), окружение, чекпоинты.

**Python-зависимости (ставятся только на GPU-машине):**

```bash
pip install torch transformers peft datasets accelerate bitsandbytes
```

`torch/transformers/peft/datasets/accelerate/bitsandbytes` **не входят** в
`pyproject.toml` проекта: локальной сюите и CI они не нужны, а весят
несколько гигабайт. Это осознанно опциональные зависимости обучения.

**Локально (машина разработки, без GPU):**

- Python 3.12 venv `.venv` — как для остального проекта;
- `pytest`, `ruff`, `mypy` — как обычно;
- доступен только `--dry-run` и `--check-env`.

Проверка окружения:

```bash
.\.venv\Scripts\python.exe scripts/train_lora.py --check-env
```

Печатает версию Python, платформу, наличие `torch/transformers/peft/...`
и CUDA. Локальный ожидаемый вывод: все тяжёлые пакеты — `НЕТ`,
`cuda: нет (torch не установлен)`.

---

## 3. Экспорт датасета

Правки редакторов лежат в таблице `edits` (см. `SPEC.md`); экспорт — из
корня репозитория:

```bash
# клубный уровень (целевой для первого адаптера)
.\.venv\Scripts\python.exe scripts/export_dataset.py \
    --level club --output data/lora_dataset.jsonl

# все уровни
.\.venv\Scripts\python.exe scripts/export_dataset.py \
    --output data/lora_dataset_all.jsonl
```

Экспорт стриминговый: строки пишутся по мере чтения курсора, все `edits`
в память не загружаются. Требуется доступный PostgreSQL (`.env`,
`docker compose up -d postgres`). Файл `data/` в git не коммитится.

> Для первого прогона достаточно 100–500 правок. Меньше ~50 — риск
> переобучения на стиль одного редактора; больше 2000 — имеет смысл
> увеличить `--epochs` не выше 3 и следить за eval.

Синтетический пример для локальной проверки пайплайна:
`tests/fixtures/lora_sample.jsonl` (4 записи, `level=club`).

---

## 4. Схема JSONL

Одна строка — один пример SFT. Схема строгая:
`messages` — список, роли **ровно** `system → user → assistant`,
непустые `content`, `schema_version=1`.

```json
{"messages": [
   {"role": "system", "content": "Ты — шахматный комментатор для игроков уровня 1800–2200. ..."},
   {"role": "user", "content": "исходный комментарий (original_text)"},
   {"role": "assistant", "content": "правка редактора (edited_text)"}
 ],
 "id": 11, "commentary_id": 11, "editor_id": null,
 "level": "club", "model": "qwen/qwen3-4b-2507",
 "created_at": "2026-10-28T18:49:48.710127+00:00", "schema_version": 1}
```

- `system` — текст `app/prompts/<level>.txt`; уровень берётся из
  `commentaries.level`. Экспорт читает файл через `importlib.resources`
  (`load_system_prompt`), поэтому схема работает и из wheel-пакета.
- `user` — ответ модели до правки, `assistant` — принятая правка
  (`edits.original_text` / `edits.edited_text`).
- Метаданные (`id`, `commentary_id`, `editor_id`, `level`, `model`,
  `created_at`, `schema_version`) сохраняются для аудита и фильтрации;
  обучение использует только `messages` (и `level` для статистики).

**Маскирование лосса.** `scripts/train_lora.py` токенизирует диалог
chat-шаблоном Qwen и выставляет `labels=-100` на токены
`system`/`user` + служебные токены шаблона до ответа; обучаются только
токены `assistant` (включая `</assistant>`/EOS). Поэтому модель учится
генерировать правку, а не пересказывать промпт.

Валидатор отбрасывает битый JSON, отсутствие/перестановку ролей, пустой
`content`, неизвестную `schema_version`, сообщения длиннее 50 000
символов. `--dry-run` завершается с кодом 1, если датасет пуст или
невалиден.

---

## 5. Гиперпараметры

| Параметр | Default | Комментарий |
| :--- | :--- | :--- |
| `--model` | `Qwen/Qwen3-4B-Instruct-2507` | базовая instruct-модель (та же, что в LM Studio) |
| `--lora-r` | `16` | ранг LoRA |
| `--lora-alpha` | `32` | масштаб LoRA (`alpha/r = 2`) |
| `--lora-dropout` | `0.05` | регуляризация |
| `--target-modules` | `q_proj,v_proj` | attention-проекции; для 4B этого достаточно |
| `--epochs` | `3` | 1–3; больше — переобучение на малом датасете |
| `--lr` | `2e-4` | стандарт для LoRA SFT |
| `--batch-size` | `1` | T4 16 GB + 4-bit + `max_length=2048` |
| `--grad-accum` | `8` | effective batch = 8 |
| `--max-length` | `2048` | обучающий пример `system+user+assistant` ≈ 1–1.2k токенов; inference-пакет фактов бывает до ~3.5k — для инференса нужен `--max-length 4096` (vLLM) |
| `--load-in-4bit` | включён | QLoRA; `--no-4bit` — полный bf16 (нужно ≥ 24 GB) |
| `--gradient-checkpointing` | включён | экономит VRAM ценой ~20–30 % скорости |

Дополнительно: `--limit N` (взять первые N записей), `--system-prompt
FILE` (заменить `system` во всех записях — при смене версии промпта),
`--seed`, `--logging-steps`.

Логика обучения (`train()`):

- импорты `torch/transformers/peft/...` — только внутри `train()`,
  поэтому `--dry-run` не требует тяжёлых пакетов;
- явные `SystemExit` с инструкцией: нет зависимостей, нет CUDA, нет/
  пуст/невалиден `--dataset`, CUDA OOM;
- `gradient_checkpointing` + `use_cache=False`, `paged_adamw_8bit` для
  QLoRA, cosine-шедулер, `save_strategy=epoch`, `report_to=[]`;
- по окончании — статистика trainable-параметров, сохранение адаптера.

---

## 6. Локальная проверка `--dry-run`

`--dry-run` не импортирует torch и работает офлайн: валидирует JSONL,
считает записи, распределение по уровням, грубую оценку токенов
(≈4 символа/токен) и печатает гиперпараметры.

```bash
.\.venv\Scripts\python.exe scripts/train_lora.py --dry-run \
    --dataset tests/fixtures/lora_sample.jsonl
```

Фактический вывод (2026-10-29, машина без GPU), exit code **0**:

```text
Датасет: tests\fixtures\lora_sample.jsonl
Записей: 4
Распределение по уровням: club=4
Оценка токенов: всего ~880, в среднем ~220 на запись, максимум ~225
Гиперпараметры: model=Qwen/Qwen3-4B-Instruct-2507, r=16, alpha=32, dropout=0.05, target_modules=q_proj,v_proj, epochs=3, batch=1, grad_accum=8 (effective=8), lr=0.0002, max_length=2048, 4bit=да, gradient_checkpointing=да
DRY-RUN OK: схема JSONL корректна, статистика посчитана, обучение не запускалось.
```

Запуск без `--dry-run` на машине без `torch` завершается **exit code 1**
и понятной инструкцией (проверено локально):

```text
Нет зависимостей для обучения LoRA: нужны torch, transformers, peft,
datasets, accelerate (и bitsandbytes для --load-in-4bit).
Локальный venv без NVIDIA GPU/CUDA обучение не запускает — это ожидаемо.
Установите на GPU-машине (RunPod/Colab):
  pip install torch transformers peft datasets accelerate bitsandbytes
Инструкция: docs/development/reports/LORA_TRAINING.md
```

---

## 7. Реальное обучение на GPU

### 7.1. RunPod (рекомендуется)

1. Pod: GPU **RTX 4090 24 GB** или **A100 40 GB**, образ
   `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` (или
   аналогичный с CUDA ≥ 12.1), диск ≥ 30 GB.
2. Клонировать репозиторий и поставить зависимости:

   ```bash
   git clone <repo-url> /workspace/ai-chess-annotator
   cd /workspace/ai-chess-annotator
   pip install -e ".[dev]"   # зависимости проекта (export/evaluate)
   pip install torch transformers peft datasets accelerate bitsandbytes
   ```

3. Перенести датасет с локальной машины (scp / RunPod upload):
   `data/lora_dataset.jsonl` → `/workspace/ai-chess-annotator/data/`.
4. Проверить окружение и датасет:

   ```bash
   python scripts/train_lora.py --check-env
   python scripts/train_lora.py --dry-run --dataset data/lora_dataset.jsonl
   ```

5. Запустить обучение:

   ```bash
   python scripts/train_lora.py \
       --dataset data/lora_dataset.jsonl \
       --model Qwen/Qwen3-4B-Instruct-2507 \
       --output-dir models/lora-club-v1 \
       --epochs 3 --batch-size 1 --grad-accum 8 \
       --lora-r 16 --lora-alpha 32 --lr 2e-4 \
       --load-in-4bit
   ```

   Без 4-bit (если 24+ GB и нужен bf16): `--no-4bit`.
6. Скачать `models/lora-club-v1/` (архив) на локальную машину. На pod
   можно не хранить: после `save_pretrained` достаточно адаптера
   (десятки МБ для `r=16`).

**Оценка времени (4B, QLoRA, T4/4090):**

| Записей | Эпох | Время |
| :--- | :--- | :--- |
| 100 | 3 | ~0.3–0.6 ч |
| 500 | 3 | ~0.6–1.2 ч |
| 2000 | 3 | ~1.5–3 ч |

### 7.2. Google Colab (T4 free)

```python
!pip install -q torch transformers peft datasets accelerate bitsandbytes
!git clone <repo-url> /content/ai-chess-annotator
%cd /content/ai-chess-annotator
# загрузить data/lora_dataset.jsonl в /content/ai-chess-annotator/data/
!python scripts/train_lora.py --dataset data/lora_dataset.jsonl \
    --output-dir /content/models/lora-club-v1 --load-in-4bit
```

T4 free — 16 GB, хватает для `batch=1`, `grad-accum=8`,
`max_length=2048`; сессия ограничена ~12 ч (прогоны 1–3 ч помещаются).
После обучения скачать `/content/models/lora-club-v1` или сохранить на
Google Drive.

### 7.3. Контроль прогресса

В stdout — `loss` каждые `--logging-steps`, статистика
`trainable/ total` параметров, сохранение по эпохам в `--output-dir`.
Хороший признак: loss падает с ~1.5–2.0 до ~0.8–1.2 за 2–3 эпохи.
Если loss < 0.5 — вероятно переобучение на малом датасете: уменьшите
`--epochs`, проверьте eval.

---

## 8. Стоимость

| Вариант | Цена | Время | Итого |
| :--- | :--- | :--- | :--- |
| RunPod RTX 4090 24 GB | ~$0.4–0.7/ч | 1–3 ч | **~$0.5–2.0** |
| RunPod A100 40 GB | ~$1.2–1.9/ч | 0.5–2 ч | ~$1–4 |
| Colab T4 | free | 1–3 ч | **$0** |

Практика: первый прогон — Colab T4 или RunPod 4090 на 1 час. Обучение
идемпотентно (фиксированный `--seed`), повторный прогон стоит столько же;
храните датасет и конфиг, чтобы воспроизвести.

---

## 9. Сохранение адаптера

`--output-dir` по умолчанию — `models/lora-club-v1` (в git не
коммитится; `models/` — локальные артефакты). После обучения там:

```
models/lora-club-v1/
├── adapter_config.json          # r=16, alpha=32, target_modules, base_model_name_or_path
├── adapter_model.safetensors    # веса LoRA (для r=16 ~10–40 MB)
├── README.md
├── tokenizer_config.json
├── tokenizer.json
└── special_tokens_map.json
```

`adapter_config.json` — маркер «готового» адаптера: `Settings.lora_model`
считает адаптер доступным только если путь — файл или каталог с этим
файлом (см. §10). Каталог без `adapter_config.json` (недокачанный,
созданный до `save_pretrained`) считается отсутствующим.

Проверка:

```powershell
Get-ChildItem models/lora-club-v1
.\.venv\Scripts\python.exe -c "from app.config import Settings; print(Settings(lora_adapter_path='models/lora-club-v1').lora_model)"
# ожидаемо: lora-club-v1
```

---

## 10. Подключение через vLLM

LM Studio, которым проект пользуется локально, **не поддерживает**
динамические LoRA через OpenAI-совместимый API. Адаптер обслуживает
vLLM:

```bash
# GPU-машина/сервер с адаптером
vllm serve Qwen/Qwen3-4B-Instruct-2507 \
    --lora-modules lora-club-v1=models/lora-club-v1 \
    --max-model-len 8192 \
    --port 8034
```

Затем в `.env` приложения:

```dotenv
VLLM_BASE_URL=http://127.0.0.1:8034/v1
LORA_ADAPTER_PATH=models/lora-club-v1
# при необходимости — VLLM_MODEL=Qwen/Qwen3-4B-Instruct-2507
```

Контракт: `Settings.lora_model` возвращает **basename** пути
(`lora-club-v1`) только когда адаптер существует; этот basename должен
совпадать с именем в `--lora-modules <name>=<path>`. `app/llm/factory.py`
для уровней `beginner`/`club` запрашивает `cfg.lora_model`; при найденном
адаптере пишет в лог `llm_lora_adapter_enabled` (adapter_path, model),
при заданном, но отсутствующем пути — `warning
llm_lora_adapter_unavailable` и работает базовой моделью. Для `gm`
поведение не меняется (DeepSeek).

**Проверка без БД:**

```bash
curl -s http://127.0.0.1:8034/v1/models | python -m json.tool
# в списке должно быть "lora-club-v1"
```

**Откат:** убрать `LORA_ADAPTER_PATH` (или остановить vLLM с
`--lora-modules`) — приложение вернётся к `VLLM_MODEL` без изменения
кода. Эксплуатационный риск минимален, потому что проверка существования
пути не даёт уйти в «model not found».

---

## 11. Оценка качества

До/после обучения — `scripts/evaluate_llm.py` (LLM-as-judge, те же
5 партий и рубрика, что в KI-030):

```bash
.\.venv\Scripts\python.exe scripts/evaluate_llm.py \
    --games 5 --levels club --timeout-s 300
```

Скрипт пишет `docs/development/reports/LLM_EVAL.md` и
`llm_eval_results.json`. Baseline (итерация 16, промпт V3, до LoRA):
`club` — **4.40/10** (факт. точность 4.00, полезность 4.40, стиль 4.80).
Цель LoRA — рост прежде всего `factual_accuracy` и `usefulness` ≥ +0.5
при той же латентности. Критерий приёмки — средняя ≥ 7/10 (порог KI-030);
промежуточная цель — ≥ 5.5/10 без регрессии стиля.

Для повторной оценки без повторной генерации используйте
`scripts/rejudge_llm.py --min-commentary-id <id>`: он берёт сохранённые
`commentaries` и оценивает их тем же судьёй с полным пакетом фактов.
Оценивать нужно **не менее 10–20 партий**, иначе разница в пределах шума
судьи.

---

## 12. Ограничения и риски

- **KI-030 остаётся Open.** Причина — слабая атрибуция у 4B-модели;
  LoRA — гипотеза улучшения, а не гарантия. Если после 1–2 прогонов
  прироста нет, следующий шаг по плану KI-030 — `qwen3-8b` или облачная
  mini-модель с бюджетом задержки.
- **GPU локально нет.** На машине разработки нет CUDA и
  `torch/peft/transformers`; реальное обучение возможно только в
  RunPod/Colab. DoD T-104 допускает каркас + инструкцию + `--dry-run`.
- **Датасет — только `edits`** (`T-034`): нет PII-редакции, checksum,
  аудита выгрузки, фоновых задач. В `user`/`assistant` могут быть имена
  игроков и редакторов; перед публикацией адаптера — проверить выгрузку.
- **Стиль ≠ факты.** LoRA учит форме ответа, но не даёт новых знаний о
  позиции; валидатор SAN и «пакет фактов» остаются обязательными.
  Выдуманные ходы по-прежнему отбрасываются `validator`.
- **SFT-пара — «переписать», а не «сгенерировать из фактов» (KI-039).**
  `export_dataset.py` кладёт в `user` исходный ответ модели
  (`edits.original_text`), а в `assistant` — принятую правку
  (`edits.edited_text`). Такой адаптер учится редактировать готовый
  комментарий, а не писать его из пакета фактов; на инференсе пакет
  фактов в `user` не подаётся. Для роста фактической точности (цель
  KI-030) перед первым GPU-прогоном нужно решить: либо перейти на
  `schema_version=2` с `fact_sheet → commentary`, либо принять
  rewrite-постановку и подавать на инференсе «черновик модели + правка».
- **Мало данных — переобучение.** 100–500 правок одного редактора дают
  стиль одного человека. Расширяйте выборку и меряйте eval, а не loss.
- **Версии пакетов.** `transformers`/`peft`/`bitsandbytes` быстро
  меняются: фиксируйте версии в логах GPU-прогона (`pip freeze`), иначе
  адаптер может не загрузиться другой версией vLLM.
- **`--max-length`.** Если prompt (system+user) не оставляет места
  assistant-ответу в `max_length`, `tokenize_example` кидает `ValueError`,
  а обучение **останавливается целиком** (`SystemExit` «Не удалось
  подготовить датасет»). Увеличьте `--max-length` или сократите промпт;
  `--dry-run` показывает оценку токенов заранее.
- **Лицензия базовой модели** (Qwen) и требования vLLM — проверить перед
  публикацией адаптера.

---

## 13. Траблшутинг

| Симптом | Причина | Решение |
| :--- | :--- | :--- |
| `Нет зависимостей для обучения LoRA` (exit 1) | локальный venv без torch | это ожидаемо; используйте GPU-машину или `--dry-run` |
| `CUDA/GPU не найдена` (exit 1) | нет NVIDIA GPU/драйвера | RunPod/Colab; локально — только dry-run |
| `CUDA OOM` (exit 1) | мало VRAM | `--batch-size 1`, `--max-length 1024`, `--load-in-4bit`, GPU больше |
| `bitsandbytes` не ставится на Windows | нет официальной сборки | WSL2 / Linux / Colab |
| `prompt занимает N токенов…` | `--max-length` мал | увеличить `--max-length` (до 4096) или сократить промпт |
| vLLM: `model "lora-club-v1" not found` | имя в `--lora-modules` не совпадает с basename | `--lora-modules lora-club-v1=<точный путь>`; проверить `ls` каталога |
| `llm_lora_adapter_unavailable` в логах | путь не существует / нет `adapter_config.json` | проверить `LORA_ADAPTER_PATH` (в контейнере — смонтированный путь) |
| Скачивание Qwen зависает | нет доступа к Hugging Face | `HF_TOKEN`, предварительно скачать модель на pod |

---

## Ссылки

- `scripts/train_lora.py` — обучение, `--dry-run`, `--check-env`.
- `scripts/export_dataset.py` — экспорт `edits` → JSONL.
- `app/config.py` — `lora_adapter_path`, `lora_adapter_available`,
  `lora_model`.
- `app/llm/factory.py` — маршрутизация и лог адаптера.
- `scripts/evaluate_llm.py` — LLM-as-judge оценка.
- `docs/KNOWN_ISSUES.md` — KI-030 (качество LLM), KI-034 (semantic cache).
- `docs/development/reports/LLM_EVAL.md` — метрики до/после.
- `docs/development/SPEC.md` §3 — маршрутизация LLM по уровням.
