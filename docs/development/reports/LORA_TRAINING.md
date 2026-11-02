# LoRA-дообучение Qwen-4B на правках редакторов (T-104)

**Статус:** пайплайн готов к запуску на GPU; локально обучение не
выполнялось (на машине разработки нет NVIDIA GPU и `torch`/`peft` —
см. §12 «Ограничения»). `scripts/train_lora.py --dry-run` работает
офлайн и проверяет датасет/гиперпараметры.

**Схема датасета — v2 (KI-039):** `user` — сериализованный пакет фактов
(`build_fact_package`), `assistant` — правка редактора. Это та же
постановка, что и на инференсе (`POST /v1/commentary` подаёт модели пакет
фактов), поэтому адаптер учится писать комментарий из фактов, а не
переписывать готовый текст. Legacy-схема v1 (`original_text → edited_text`,
rewrite) сохранена флагом `--schema-version 1` для сравнения.

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
рекомендаций. LoRA в итерации 48 дала 3B **2.87/10** и 7B **3.33/10** —
хуже baseline; KI-030 **Closed**, адаптеры отклонены, у 7B языковой
дрейф (KI-060). Промпт-инженерия (итерация 16) дала потолок; следующий
шаг — пересборка golden dataset (DeepSeek) и повторная LoRA на 7B
(T-104b): модель учится писать в стиле принятых правок, а не «сырых»
ответов.

Пайплайн (schema_version=2, KI-039):

```
edits + games/moves/motifs (PostgreSQL)
        │  scripts/export_dataset.py --schema-version 2   (default)
        │    games.pgn → parse_pgn → load_analyses → build_fact_package
        ▼
data/lora_dataset.jsonl  (system → user=пакет фактов → assistant=правка)
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

**Почему v2, а не v1.** При инференсе
`app/commentary/service.py::generate_commentary` подаёт модели именно
пакет фактов (`build_fact_package`). v1-датасет учил другому контракту:
`user` = готовый комментарий, `assistant` = его правка (rewrite). Адаптер,
обученный на v1, не умеет писать из фактов — на инференсе он видел бы
вход, которого не было в обучении. v2 выравнивает train и inference:
`user` — тот же пакет фактов, что уходит модели в проде.

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

Правки редакторов лежат в таблице `edits` (см. `SPEC.md`); для v2 к ним
подтягиваются `games.pgn`, `moves` и `motifs`, из которых собирается
пакет фактов. Экспорт — из корня репозитория:

```bash
# v2 (default): user = пакет фактов, assistant = правка
.\.venv\Scripts\python.exe scripts/export_dataset.py \
    --level club --output data/lora_dataset.jsonl

# то же самое, версия схемы задана явно
.\.venv\Scripts\python.exe scripts/export_dataset.py \
    --level club --schema-version 2 --output data/lora_dataset.jsonl

# v1 legacy (rewrite): user = original_text, assistant = edited_text
.\.venv\Scripts\python.exe scripts/export_dataset.py \
    --level club --schema-version 1 --output data/lora_dataset_v1.jsonl
```

Требуется доступный PostgreSQL (`.env`, `docker compose up -d postgres`).
Файл `data/` в git не коммитится.

**Статистика и пропуски.** Для v2 экспорт печатает сводку и не падает на
проблемных записях — они пропускаются с предупреждением в stderr и
счётчиком по причине:

```text
Экспорт датасета: schema_version=2
Экспортировано записей: 1
Пропущено записей: 2
  PGN не разобран (InvalidPgnError): 1
  нет анализа ходов (moves пусты): 1
Распределение по уровням: club=1
Размер пакета фактов: в среднем 1200 символов, всего 1200
Файл: data/lora_dataset.jsonl
```

Причины (`scripts/export_dataset.py`): `no_game` — нет строки `games`
(битая ссылка), `invalid_pgn` — `parse_pgn` бросил `InvalidPgnError`,
`no_analysis` — у партии нет `moves` (не вызывался `/v1/analyze`),
`invalid_level` — уровень без промпта. Записи без анализа не
экспортируются намеренно: пустой пакет фактов учил бы модель отвечать
без фактов.

v2 читает `edits` батчами по `edits.id` (keyset-пагинация), а не одним
стриминговым курсором: внутри обработки записи нужны запросы
`load_analyses`, а asyncpg не допускает параллельные операции на одном
соединении. v1 по-прежнему стриминговый.

> Для первого прогона достаточно 100–500 правок. Меньше ~50 — риск
> переобучения на стиль одного редактора; больше 2000 — имеет смысл
> увеличить `--epochs` не выше 3 и следить за eval.

Синтетические примеры для локальной проверки пайплайна:

- `tests/fixtures/lora_sample_v2.jsonl` — 4 записи v2, `user` собран
  реальным `build_fact_package` из двух коротких партий;
- `tests/fixtures/lora_sample.jsonl` — 4 записи v1 (legacy, rewrite) —
  удобно проверять предупреждение `--dry-run` и обратную совместимость.

---

## 4. Схема JSONL

Одна строка — один пример SFT. `messages` — список, роли **ровно**
`system → user → assistant`, непустые `content`. Поддерживаются две версии
схемы: `schema_version=2` (актуальная) и `schema_version=1` (legacy).

### 4.1. v2: пакет фактов → комментарий (default)

```json
{"messages": [
   {"role": "system", "content": "Ты — шахматный комментатор для игроков уровня 1800–2200. ..."},
   {"role": "user", "content": "# Партия\nБелые: Player A\nЧёрные: Player B\n...\n# Ходы партии (SAN)\n1. e4 e5 ...\n\n# Ключевые моменты (до 3, по убыванию потери)\n## 1. Ход 10 чёрных: Be6 (полуход 20)\n- Сыграно: Be6 (чёрные)\n..."},
   {"role": "assistant", "content": "Правка редактора: комментарий в принятом стиле"}
 ],
 "schema_version": 2,
 "metadata": {"id": 201, "commentary_id": 61, "game_id": 41,
              "editor_id": 1, "level": "club",
              "model": "qwen/qwen3-4b-2507",
              "created_at": "2026-10-29T09:00:00+00:00",
              "fact_sheet_chars": 1196}}
```

- `system` — текст `app/prompts/<level>.txt`; уровень берётся из
  `commentaries.level`. Экспорт читает файл через `importlib.resources`
  (`load_system_prompt`), поэтому схема работает и из wheel-пакета.
- `user` — **пакет фактов**, собранный ровно тем же кодом, что и на
  инференсе: `game.pgn → parse_pgn → load_analyses(session, game, parsed)
  → build_fact_package(parsed, analyses, level)`. Структура: `# Партия`,
  `# Ходы партии (SAN)`, `# Ключевые моменты` (оценки движка, лучший ход,
  мотивы, рекомендация Policy; PV — только для `gm`).
- `assistant` — принятая правка редактора (`edits.edited_text`).
- `metadata` — вложенный объект (не плоские поля, как в v1): `id`,
  `commentary_id`, `game_id`, `editor_id`, `level`, `model`, `created_at`,
  `fact_sheet_chars` (длина `user`). Обучение использует только
  `messages`; `metadata` — для аудита, фильтрации и контроля размера
  пакета.

**Маскирование лосса.** `scripts/train_lora.py` токенизирует диалог
chat-шаблоном Qwen и выставляет `labels=-100` на токены `system`/`user` +
служебные токены шаблона до ответа; обучаются только токены `assistant`
(включая `</assistant>`/EOS). В v2 весь пакет фактов (потенциально
сотни-тысячи токенов) замаскирован: модель учится **генерировать**
комментарий по фактам, а не пересказывать пакет. Токенизация, граница
prompt/assistant и `collate_batch` релиза v1 не менялись.

**Валидация.** `train_lora.py` принимает `schema_version ∈ {1, 2}`
(`SUPPORTED_SCHEMA_VERSIONS`); отсутствие поля трактуется как v1. Для v2
требуется объект `metadata`, `metadata.fact_sheet_chars` (если задан) —
целое ≥ 0. Дополнительно работает лёгкая эвристика
`looks_like_fact_package`: если в v2 `user` нет заголовков `# Партия` и
`# Ключевые моменты`, `--dry-run` печатает предупреждение (запись при
этом остаётся валидной — эвристика ловит перепутанные выгрузки, а не
заменяет схему). Битый JSON, перестановка ролей, пустой `content`,
сообщения длиннее 50 000 символов отбрасываются; неизвестная версия
схемы — ошибка (exit 1).

### 4.2. v1 legacy: original_text → edited_text (rewrite)

```json
{"messages": [
   {"role": "system", "content": "..."},
   {"role": "user", "content": "исходный комментарий (original_text)"},
   {"role": "assistant", "content": "правка редактора (edited_text)"}
 ],
 "id": 11, "commentary_id": 11, "editor_id": null,
 "level": "club", "model": "qwen/qwen3-4b-2507",
 "created_at": "2026-10-28T18:49:48.710127+00:00", "schema_version": 1}
```

Схема оставлена для сравнения и для старых выгрузок. **Она учит rewrite:**
модель видит готовый текст и правит его, а на инференсе получает пакет
фактов. `--dry-run` явно предупреждает об этом (см. §6). Смешивать v1 и
v2 в одном датасете технически можно (`SUPPORTED_SCHEMA_VERSIONS`), но
содержательно это две разные задачи — для нового адаптера используйте
только v2.

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
| `--max-length` | `2048` | v2: `system` + пакет фактов + ответ. На синтетической фикстуре ~1057 токенов на запись; на реальных партиях пакет длиннее (для `gm` с PV — до ~3–4k), поэтому смотрите оценку в `--dry-run` и при необходимости берите `--max-length 4096` (и тот же лимит для vLLM на инференсе) |
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
считает записи, распределение по уровням и по `schema_version`, грубую
оценку токенов (≈4 символа/токен), печатает гиперпараметры и
предупреждения по составу датасета.

```bash
.\.venv\Scripts\python.exe scripts/train_lora.py --dry-run \
    --dataset tests/fixtures/lora_sample_v2.jsonl
```

Фактический вывод (2026-10-29, машина без GPU), exit code **0**:

```text
Датасет: tests\fixtures\lora_sample_v2.jsonl
Записей: 4
Распределение по уровням: club=4
Распределение по schema_version: 2=4
Оценка токенов: всего ~4228, в среднем ~1057 на запись, максимум ~1076
Гиперпараметры: model=Qwen/Qwen3-4B-Instruct-2507, r=16, alpha=32, dropout=0.05, target_modules=q_proj,v_proj, epochs=3, batch=1, grad_accum=8 (effective=8), lr=0.0002, max_length=2048, 4bit=да, gradient_checkpointing=да
DRY-RUN OK: схема JSONL корректна, статистика посчитана, обучение не запускалось.
```

v2-фикстура корректна, поэтому предупреждений нет. Для legacy-датасета
(v1) тот же запуск печатает распределение `1=…` и предупреждение о
rewrite-семантике:

```bash
.\.venv\Scripts\python.exe scripts/train_lora.py --dry-run \
    --dataset tests/fixtures/lora_sample.jsonl
```

```text
Записей: 4
Распределение по уровням: club=4
Распределение по schema_version: 1=4
...
ВНИМАНИЕ: schema_version=1: 4 записей — user содержит original_text, модель учится ПЕРЕПИСЫВАТЬ готовый комментарий (rewrite), а не писать его из пакета фактов. Для новой постановки переэкспортируйте: scripts/export_dataset.py --schema-version 2
DRY-RUN OK: схема JSONL корректна, статистика посчитана, обучение не запускалось.
```

Если v2-запись содержит `user` без заголовков пакета фактов, появится
предупреждение `schema_version=2, но user не похож на пакет фактов …` —
это признак перепутанной выгрузки (например, файл экспортирован с
`--schema-version 1`, а версия в строках поправлена вручную).

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
   Датасет должен быть экспортирован с `--schema-version 2` (default):
   первая строка `user` содержит `# Партия`.
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

- **KI-030 закрыт в итерации 48.** LoRA на 3B и 7B дала 2.87 и 3.33/10
  против 4.40/10 у zero-shot 4B — прироста нет; адаптеры отклонены,
  возврат к 4B. Выявлено переобучение и языковой дрейф 7B (KI-060).
  Следующий шаг — пересобрать golden dataset более сильной моделью
  (DeepSeek) и повторить обучение/оценку (T-104b). Прироста может не быть
  и там; решение принимается по eval, а не по факту обучения.
- **GPU локально нет.** На машине разработки нет CUDA и
  `torch/peft/transformers`; реальное обучение возможно только в
  RunPod/Colab. DoD T-104 допускает каркас + инструкцию + `--dry-run`.
- **Датасет — это `edits` + факты партии** (`T-034`, KI-039): по-прежнему
  нет PII-редакции, checksum и аудита выгрузки. В v2 `user` — пакет
  фактов, а он содержит имена игроков (`White`/`Black` из PGN) и мотивы;
  в `assistant` — текст редактора. Перед публикацией адаптера проверьте
  выгрузку на PII; для внешних LLM (RULES §7.3) пакет фактов уже
  отправляется без user_id, но с метаданными партии.
- **Стиль ≠ факты.** LoRA учит форме ответа, но не даёт новых знаний о
  позиции; валидатор SAN и «пакет фактов» остаются обязательными.
  Выдуманные ходы по-прежнему отбрасываются `validator`.
- **v1-датасет несовместим с инференсом (KI-039).** `--schema-version 1`
  кладёт в `user` исходный ответ модели (`edits.original_text`), поэтому
  адаптер учится rewrite, а не генерации из фактов; `--dry-run`
  предупреждает об этом. Для нового адаптера используйте v2 (default);
  v1 оставлен только для сравнения «rewrite vs fact_sheet→commentary».
- **Размер пакета фактов.** v2 prompt существенно длиннее v1 (на
  синтетике ~1057 токенов против ~220): больше VRAM на батч и времени на
  эпоху. Реальные пакеты могут быть ещё длиннее (особенно `gm` с PV),
  поэтому перед прогоном смотрите `--dry-run` и при необходимости
  увеличивайте `--max-length` (до 4096).
- **Записи без анализа не экспортируются.** Экспорт v2 пропускает
  `edits`, для партий которых нет `moves` (не вызывался `/v1/analyze`),
  и печатает статистику пропусков. На первом прогоне проверьте, что
  `Пропущено записей` невелико: иначе датасет перекошен в сторону
  нескольких проанализированных партий.
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
| `ВНИМАНИЕ: schema_version=1 … ПЕРЕПИСЫВАТЬ` | датасет экспортирован с `--schema-version 1` | переэкспортировать с `--schema-version 2` (default) |
| `schema_version=2, но user не похож на пакет фактов` | файл собран не `export_dataset.py` / схемы перепутаны | переэкспортировать v2; в `user` должны быть `# Партия` и `# Ключевые моменты` |
| `для schema_version=2 нет объекта metadata` (exit 1) | ручная/старая выгрузка с v2 без `metadata` | переэкспортировать `--schema-version 2` или добавить `metadata` |
| `Пропущено записей: N` при экспорте v2 | у партии нет `moves` или битый PGN | выполнить `/v1/analyze` для партий; причина по каждой записи — в stderr |
| vLLM: `model "lora-club-v1" not found` | имя в `--lora-modules` не совпадает с basename | `--lora-modules lora-club-v1=<точный путь>`; проверить `ls` каталога |
| `llm_lora_adapter_unavailable` в логах | путь не существует / нет `adapter_config.json` | проверить `LORA_ADAPTER_PATH` (в контейнере — смонтированный путь) |
| Скачивание Qwen зависает | нет доступа к Hugging Face | `HF_TOKEN`, предварительно скачать модель на pod |

---

## Ссылки

- `scripts/train_lora.py` — обучение, `--dry-run`, `--check-env`,
  `SUPPORTED_SCHEMA_VERSIONS`, эвристика пакета фактов.
- `scripts/export_dataset.py` — экспорт `edits` → JSONL (`--schema-version
  1|2`, статистика пропусков).
- `app/commentary/service.py` — `load_analyses`: восстановление
  `MoveAnalysis` из `moves`/`motifs` для пакета фактов.
- `app/llm/prompts.py` — `build_fact_package` (контракт `user` в v2).
- `app/config.py` — `lora_adapter_path`, `lora_adapter_available`,
  `lora_model`.
- `app/llm/factory.py` — маршрутизация и лог адаптера.
- `scripts/evaluate_llm.py` — LLM-as-judge оценка.
- `docs/KNOWN_ISSUES.md` — KI-030 (качество LLM), KI-034 (semantic cache),
  KI-039 (dataset schema v2).
- `docs/development/reports/LLM_EVAL.md` — метрики до/после.
- `docs/development/SPEC.md` §3 — маршрутизация LLM по уровням.
